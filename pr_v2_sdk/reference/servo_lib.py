import logging
import serial
import serial.tools.list_ports
from uservo import UartServoManager


class ServoController:
    def __init__(self):
        """
        初始化 ServoController。
        功能：自动检测 COM 口并连接。
        """
        self.SERVO_BAUDRATE = 115200

        # === 自动检测 COM 口 ===
        self.SERVO_PORT_NAME = self.find_servo_port()

        # 初始化串口
        try:
            self.uart = serial.Serial(
                port=self.SERVO_PORT_NAME,
                baudrate=self.SERVO_BAUDRATE,
                parity=serial.PARITY_NONE,
                stopbits=1,
                bytesize=8,
                timeout=0
            )
            logging.info(f"串口初始化成功: {self.SERVO_PORT_NAME}")
        except serial.SerialException as e:
            logging.error(f"串口初始化失败 ({self.SERVO_PORT_NAME}): {e}")
            raise e

        # 初始化舵机管理器
        try:
            self.uservo = UartServoManager(self.uart)
            logging.info("舵机管理器初始化成功")
        except Exception as e:
            logging.error(f"舵机管理器初始化失败: {e}")
            raise e

    def find_servo_port(self):
        """
        自动寻找可能的舵机串口 (Windows/Linux通用)
        """
        ports = list(serial.tools.list_ports.comports())
        target_port = None

        # 常见的USB转串口芯片标识
        keywords = ["CH340"]

        logging.info("正在扫描串口设备...")

        if not ports:
            error_msg = "未发现任何串口设备，请检查USB连接。"
            logging.error(error_msg)
            raise Exception(error_msg)

        # 1. 优先匹配包含关键字的端口
        for p in ports:
            logging.info(f"发现设备: {p.device} - {p.description}")
            for key in keywords:
                if key in p.description.upper():
                    target_port = p.device
                    logging.info(f"匹配到目标端口: {target_port} (匹配关键字: {key})")
                    return target_port

        # 2. 如果没有匹配到关键字，但存在端口，默认尝试第一个
        if target_port is None:
            logging.warning(f"未匹配到已知芯片")
            return target_port

        return target_port

    def set_servos_speed(self, servo_ids, is_cw, mean_dps):
        for servo_id in servo_ids:
            self.uservo.set_wheel_norm(servo_id, is_cw=is_cw, mean_dps=mean_dps)
            logging.info(f"   设置舵机[{servo_id}] 速度:{mean_dps} 方向:{'CW' if is_cw else 'CCW'}")

    def set_servos_angle(self, servo_ids, angle, velocity,  power):
        for servo_id in servo_ids:
            self.uservo.set_servo_angle(servo_id, angle, velocity=velocity, power=power)
            logging.info(f"[{servo_id}]设置舵机角度功能-->{angle}")

    def set_servos_damping(self, servo_ids, power):
        for servo_id in servo_ids:
            self.uservo.set_damping(servo_id, power=power)
            logging.info(f"[{servo_id}]已设置为设置阻尼模式")

    def stop(self, servo_ids):
        for servo_id in servo_ids:
            self.uservo.disable_torque(servo_id)
        logging.info(f"停止舵机功能: {servo_ids}")

    def wait(self, timeout=None):
        self.uservo.wait(timeout)

    def read_angle(self, servo_id):
        angle_current = self.uservo.query_servo_angle(servo_id)
        return angle_current

    def spread(self, servo_ids_angle_head, servo_ids_angle_tail, head_angle, tail_angle, mean_dps):
        """张开 (关节模式)"""
        for servo_id in servo_ids_angle_head:
            self.uservo.set_servo_angle(servo_id=servo_id, angle=head_angle, velocity=400, is_mturn=True, power=5000, mean_dps=mean_dps)
        for servo_id in servo_ids_angle_tail:
            self.uservo.set_servo_angle(servo_id=servo_id, angle=tail_angle, velocity=400, is_mturn=True, power=5000, mean_dps=mean_dps)
        logging.info(f"ServoController: 张开命令接收 (Head:{servo_ids_angle_head}, Tail:{servo_ids_angle_tail})")

    def shrink(self, servo_ids_angle_head, servo_ids_angle_tail, head_angle, tail_angle, mean_dps):
        """收缩 (关节模式)"""
        for servo_id in servo_ids_angle_head:
            self.uservo.set_servo_angle(servo_id=servo_id, angle=head_angle, is_mturn=True, power=4000, mean_dps=mean_dps)
        for servo_id in servo_ids_angle_tail:
            self.uservo.set_servo_angle(servo_id=servo_id, angle=tail_angle, is_mturn=True, power=4000, mean_dps=mean_dps)
        logging.info(f"ServoController: 收缩命令接收 (Head:{servo_ids_angle_head}, Tail:{servo_ids_angle_tail})")

    def status(self, servo_ids):
        """打印舵机状态"""
        for servo_id in servo_ids:
            voltage = self.uservo.query_voltage(servo_id)
            current = self.uservo.query_current(servo_id)
            power = self.uservo.query_power(servo_id)
            temp = self.uservo.query_temperature(servo_id)
            angle = self.uservo.query_servo_angle(servo_id)
            status = self.uservo.query_status(servo_id)

            print(
                "[ID: {:4.1f}]; Voltage: {:4.1f}V; Current: {:4.1f}A; Power: {:4.1f}W; T: {:2.0f}; Angle: {:4.1f}deg; "
                "Status: {:08b}".format(
                    servo_id, voltage, current,
                    power, temp, angle,
                    status),
                end='\n')