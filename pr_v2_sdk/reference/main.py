#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
import logging
import keyboard

from servo_lib import ServoController
from gimbal_lib import Gimbal
from dcmotor_lib import DCMotorController


class RobotController:
    def __init__(self):
        self.running = True

        # ===== 初始化日志 =====
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

        # ===== 初始化硬件 =====
        try:
            self.servo = ServoController()
            logging.info("✓ Servo 初始化成功")
        except Exception as e:
            logging.error(f"Servo 初始化失败: {e}")
            self.servo = None

        try:
            # 共享舵机实例给 Gimbal 避免串口冲突
            self.gimbal = Gimbal(uservo=self.servo.uservo if self.servo else None)
            logging.info("✓ Gimbal 初始化成功")
        except Exception as e:
            logging.error(f"Gimbal 初始化失败: {e}")
            self.gimbal = None

        try:
            # DC 电机
            excluded_port = getattr(self.servo, 'SERVO_PORT_NAME', None)
            self.motor_ctrl = DCMotorController(excluded_port=excluded_port)
            logging.info("✓ DC Motor 初始化完成")
        except Exception as e:
            logging.error(f"DC Motor 初始化失败: {e}")
            self.motor_ctrl = None

        # ===== 舵机参数 =====
        self.head_ids = [20]
        self.tail_ids = [10]

        # ===== DC 电机 ID 与方向 =====
        # 格式: (motor_id, forward_speed, backward_speed)
        # 正值正转，负值反转，根据实际安装方向调整
        self.motor_cmds = [
            ("A",  -75, 75),
            ("B",  75, -75),
            ("C",  -75, 75),
            ("D", 75,  -75),
            ("E", 75,  -75),
            ("F", 75,  -75),
        ]

    # =========================
    # 键盘控制轮询DFT WFSSXXXX
    # =========================
    def handle_key_input(self):
        # 云台水平
        if self.gimbal:
            if keyboard.is_pressed("q"):
                self.gimbal.move_h(-1)
            elif keyboard.is_pressed("e"):
                self.gimbal.move_h(1)
            # 云台俯仰
            if keyboard.is_pressed("z"):
                self.gimbal.move_p(-1)
            elif keyboard.is_pressed("c"):
                self.gimbal.move_p(1)
            # 云台回零
            if keyboard.is_pressed("r"):
                self.gimbal.home()

        # 舵机
        if self.servo:
            if keyboard.is_pressed("t"):
                self.servo.spread(
                    servo_ids_angle_head=self.head_ids,
                    servo_ids_angle_tail=self.tail_ids,
                    head_angle=2100,
                    tail_angle=1070,
                    mean_dps=400
                )
            elif keyboard.is_pressed("f"):
                self.servo.shrink(
                    servo_ids_angle_head=self.head_ids,
                    servo_ids_angle_tail=self.tail_ids,
                    head_angle=850,
                    tail_angle=-180,
                    mean_dps=400
                )
            elif keyboard.is_pressed("space"):
                self.servo.stop(self.head_ids + self.tail_ids)

        # DC 电机
        if self.motor_ctrl:
            if keyboard.is_pressed("w"):
                for mid, fwd, _ in self.motor_cmds:
                    self.motor_ctrl.send_command(f"M{mid} {fwd}")
                    time.sleep(0.01)
            elif keyboard.is_pressed("x"):
                for mid, _, bwd in self.motor_cmds:
                    self.motor_ctrl.send_command(f"M{mid} {bwd}")
                    time.sleep(0.01)
            elif keyboard.is_pressed("s"):
                self.motor_ctrl.all_stop()

        # 退出
        if keyboard.is_pressed("esc"):
            self.exit()

    # =========================
    # 主循环
    # =========================
    def run(self):
        print(
            "控制说明:\n"
            "云台: Q/E 左右, Z/C 上下, R 回零\n"
            "舵机: T 张开, F 收缩\n"
            "DC 电机: W 前进, X 倒退, S 停止\n"
            "ESC: 退出"
        )

        try:
            while self.running:
                self.handle_key_input()
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup()

    # =========================
    # 退出/清理
    # =========================
    def exit(self):
        logging.info("正在退出...")
        self.running = False

    def cleanup(self):
        if self.servo:
            self.servo.stop(self.head_ids + self.tail_ids)
        if self.gimbal:
            self.gimbal.wheel_stop()
        if self.motor_ctrl:
            self.motor_ctrl.all_stop()
            self.motor_ctrl.close()
        logging.info("✓ 已安全退出")


if __name__ == "__main__":
    robot = RobotController()
    robot.run()