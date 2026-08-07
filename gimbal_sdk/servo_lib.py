import logging
import time
import serial
import serial.tools.list_ports
from gimbal_sdk.uservo import UartServoManager


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
            # Let CH340 hardware settle after port open — avoids
            # "device reports readiness but returned no data" races.
            time.sleep(0.15)
            self.uart.reset_input_buffer()
            self.uart.reset_output_buffer()
            logging.info(f"串口初始化成功: {self.SERVO_PORT_NAME}")
        except serial.SerialException as e:
            logging.error(f"串口初始化失败 ({self.SERVO_PORT_NAME}): {e}")
            raise e

        # 初始化舵机管理器 (retry on transient failures like port races)
        last_err = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(0.5)
                    logging.info(f"舵机管理器重试 (attempt {attempt + 1}/3)...")
                self.uservo = UartServoManager(self.uart)
                logging.info("舵机管理器初始化成功")
                break
            except Exception as e:
                last_err = e
                logging.warning(f"舵机管理器初始化失败 (attempt {attempt + 1}/3): {e}")
        else:
            logging.error(f"舵机管理器初始化失败 (all retries exhausted): {last_err}")
            raise last_err

    # CH340/CH341 USB-Serial chip HWID patterns (constant across USB ports)
    _CHIP_HWIDS = [
        "USB VID:PID=1A86:7523",   # CH340
        "USB VID:PID=1A86:5512",   # CH341
        "USB VID:PID=1A86:7524",   # CH340 (alt revision)
    ]

    def find_servo_port(self):
        """
        自动寻找舵机串口 — HWID 优先匹配 CH340/CH341 芯片 (跨平台)。

        策略:
          1. HWID 精确匹配 CH340/CH341 的 USB VID:PID
          2. 描述关键字回退匹配 (driver 不提供 HWID 时)
          3. 仅有一个串口时直接使用
          4. 多个串口且全不匹配时抛出明确错误
        """
        ports = list(serial.tools.list_ports.comports())

        logging.info("正在扫描串口设备...")

        if not ports:
            error_msg = "未发现任何串口设备，请检查USB连接。"
            logging.error(error_msg)
            raise Exception(error_msg)

        for p in ports:
            desc = (p.description or "").upper()
            hwid = (p.hwid or "").upper()
            logging.info(f"发现设备: {p.device} - {p.description} (hwid={p.hwid})")

        # 1. HWID 精确匹配 (最可靠 — CH340 VID:PID 不随 USB 口变化)
        for p in ports:
            hwid = (p.hwid or "").upper()
            for sig in self._CHIP_HWIDS:
                if sig in hwid:
                    logging.info(f"匹配到目标端口 (HWID): {p.device} → {sig}")
                    return p.device

        # 2. 描述关键字回退
        for p in ports:
            desc = (p.description or "").upper()
            if "CH340" in desc or "CH341" in desc:
                logging.info(f"匹配到目标端口 (描述): {p.device} → {p.description}")
                return p.device

        # 3. 仅有一个串口设备时直接使用
        if len(ports) == 1:
            logging.warning(f"未匹配到 CH340/CH341 芯片，但仅有一个串口设备，尝试使用: {ports[0].device}")
            return ports[0].device

        # 4. 多个设备全不匹配 → 明确报错，列出所有候选
        port_list = ", ".join(f"{p.device} ({p.description})" for p in ports)
        error_msg = (
            f"发现 {len(ports)} 个串口设备，但均未匹配到 CH340/CH341 芯片。\n"
            f"  候选设备: {port_list}\n"
            f"  请确认 CH340 已连接并安装驱动，或手动指定端口。"
        )
        logging.error(error_msg)
        raise Exception(error_msg)

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