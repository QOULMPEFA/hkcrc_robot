#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import serial
import serial.tools.list_ports

class DCMotorController:

    def __init__(self, baudrate=115200, excluded_port=None):
        self.BAUDRATE = baudrate
        self.excluded_port = excluded_port
        self.PORT_NAME = self.find_motor_port()
        self.ser = None

        if self.PORT_NAME:
            try:
                self.ser = serial.Serial(
                    port=self.PORT_NAME,
                    baudrate=self.BAUDRATE,
                    timeout=1
                )
                logging.info(f"电机串口初始化成功: {self.PORT_NAME} (Baud: {self.BAUDRATE})")
            except serial.SerialException as e:
                logging.error(f"电机串口打开失败 ({self.PORT_NAME}): {e}")
        else:
            logging.warning("未找到电机串口 (CH341T/USB Serial)")

    def find_motor_port(self):
        ports = list(serial.tools.list_ports.comports())
        keywords = ["CH340", "CH341"]  # 增加 CH341 支持ww
        logging.info("正在扫描电机串口...")

        for p in ports:
            if self.excluded_port and p.device == self.excluded_port:
                continue
            for key in keywords:
                if key in p.description.upper():
                    logging.info(f"匹配到电机端口: {p.device} ({p.description})")
                    return p.device
        return None

    def send_command(self, cmd_str):
        """
        发送文本命令到板子，例如 "MA 50\n"
        """
        if self.ser and self.ser.is_open:
            try:
                if not cmd_str.endswith("\n"):
                    cmd_str += "\n"
                self.ser.write(cmd_str.encode('utf-8'))
                logging.info(f"发送指令: {cmd_str.strip()}")
            except Exception as e:
                logging.error(f"电机指令发送失败: {e}")
        else:
            logging.warning(f"电机串口未连接，无法发送: {cmd_str.strip()}")

    def all_stop(self):
        """所有电机停下"""
        self.send_command("STOP")

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            logging.info("电机串口已关闭")

