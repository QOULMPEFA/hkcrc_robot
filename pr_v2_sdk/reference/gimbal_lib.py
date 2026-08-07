#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
万向轴控制 - 精简无限位版
依赖: pip install keyboard pyserial
"""
import sys
import time
import logging
import keyboard

from servo_lib import ServoController


class Gimbal(ServoController):
    """万向轴控制器，继承自 ServoController"""
    
    # 配置常量
    SERVO_IDS = {"H1": 1, "H2": 2, "P1": 3, "P2": 4}
    STEP_ANGLE = 5       # 步进角度
    VELOCITY = 200       # 速度
    POWER = 3000         # 功率
    ANGLE_LIMIT = 300    # ±300° 限位
    KK = 0.78           # 防控制线松动系数
    
    def __init__(self, uservo=None):
        if uservo:
            self.uservo = uservo
            self.uart = uservo.uart
        else:
            super().__init__()

        self.h_angle = 0
        self.p_angle = 0
        self.running = True
        
        # 启用多圈模式并设置限位
        for sid in self.SERVO_IDS.values():
            servo = self.uservo.servos.get(sid)
            if servo:
                servo.is_mturn = True
                servo.lowerb = -self.ANGLE_LIMIT
                servo.upperb = self.ANGLE_LIMIT
        
        logging.info("✓ 舵机连接成功，限位范围: ±%d°" % self.ANGLE_LIMIT)
        self.home()

    def set_angle(self, sid, angle):
        """设置舵机角度"""
        self.uservo.set_servo_angle(
            sid, angle, 
            velocity=self.VELOCITY, 
            power=self.POWER,
            is_mturn=True
        )

    def _clamp_angle(self, angle):
        """限位检查"""
        return max(-self.ANGLE_LIMIT, min(self.ANGLE_LIMIT, angle))

    def _calc_servo_angles(self, angle):
        """计算舵机角度（防控制线松动）"""
        if angle >= 0:
            return angle, -self.KK * angle
        return self.KK * angle, -angle

    def move(self, dh, dp):
        """移动万向轴 (dh: 水平变化, dp: 俯仰变化)"""
        self.h_angle = self._clamp_angle(self.h_angle + dh * self.STEP_ANGLE)
        self.p_angle = self._clamp_angle(self.p_angle + dp * self.STEP_ANGLE)
        
        h1x, h2x = self._calc_servo_angles(self.h_angle)
        p1x, p2x = self._calc_servo_angles(self.p_angle)

        self.set_angle(self.SERVO_IDS["H1"], h1x)
        self.set_angle(self.SERVO_IDS["H2"], h2x)
        self.set_angle(self.SERVO_IDS["P1"], p1x)
        self.set_angle(self.SERVO_IDS["P2"], p2x)
        logging.info(f"水平: {self.h_angle:+.0f}°，俯仰: {self.p_angle:+.0f}°")

    def move_h(self, direction):
        """水平轴移动"""
        self.move(direction, direction)

    def move_p(self, direction):
        """俯仰轴移动"""
        self.move(direction, -direction)

    def home(self):
        """回原点"""
        for s in self.SERVO_IDS.values():
            self.set_angle(s, 0)
        self.h_angle, self.p_angle = 0, 0
        logging.info("⟲ 已回原点")

    def wheel_stop(self):
        """停止电机（关闭力矩）"""
        for sid in self.SERVO_IDS.values():
            self.uservo.wheel_stop(sid)
        logging.info("⏹ 电机已停止")

    def exit_prog(self):
        """结束程序 - 先停止电机再退出"""
        logging.info("\n⚠ 正在安全退出...")
        self.wheel_stop()
        time.sleep(0.3)
        self.running = False
        keyboard.unhook_all()
        sys.exit(0)

    def run(self):
        """主循环"""
        logging.info("\n控制: Q左转 E右转 | Z上仰 C下俯 | R原点 | X停止 | V退出")
        logging.info(f"⚠ 限位范围: ±{self.ANGLE_LIMIT}°\n")
        
        # 注册键盘钩子
        keyboard.on_press_key("q", lambda _: self.move_h(-1) if self.running else None)
        keyboard.on_press_key("e", lambda _: self.move_h(1) if self.running else None)
        keyboard.on_press_key("z", lambda _: self.move_p(-1) if self.running else None)
        keyboard.on_press_key("c", lambda _: self.move_p(1) if self.running else None)
        keyboard.on_press_key("r", lambda _: self.home() if self.running else None)
        keyboard.on_press_key("x", lambda _: self.wheel_stop() if self.running else None)
        keyboard.on_press_key("v", lambda _: self.exit_prog())
        
        try:
            while self.running:
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            if self.running:
                keyboard.unhook_all()
                self.wheel_stop()
                logging.info("✓ 已安全退出")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    g = Gimbal()
    g.run()
