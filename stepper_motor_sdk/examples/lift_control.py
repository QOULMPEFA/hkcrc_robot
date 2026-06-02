import sys
import os
import time
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ""))
from stepper_motor_sdk import CANopenNode, StepperMotor, HomingMethod


class LiftingPlatformController:
    def __init__(self, node: CANopenNode, pulses_per_rev=1000):
        # 机械参数
        self.GEARBOX_RATIO = 10.0  # 1:10 减速比
        self.PULLEY_RATIO = 1.0  # 假设皮带轮 1:1
        self.LEADSCREW_LEAD = 8.0  # 导程 8mm/rev
        self.PULSES_PER_REV = pulses_per_rev

        # 实例化驱动器
        self.motor = StepperMotor(node, pulses_per_rev=self.PULSES_PER_REV)

        # 安全状态
        self.is_stalled = False
        self.max_torque_limit = 800  # 默认 80% 额定转矩
        self._stop_monitor = threading.Event()
        self._monitor_thread = None

    def mm_to_pulses(self, height_mm: float) -> int:
        """运动学：高度(mm) -> 脉冲数"""
        # 1. 计算丝杆转动圈数
        screw_revs = height_mm / self.LEADSCREW_LEAD
        # 2. 计算电机需要转动的圈数 (考虑减速比和皮带轮)
        motor_revs = screw_revs * self.GEARBOX_RATIO * self.PULLEY_RATIO
        # 3. 转换为总脉冲数
        return int(motor_revs * self.PULSES_PER_REV)

    def _torque_monitor_loop(self):
        """转矩监控线程：150ms 时间滤波逻辑"""
        stall_start_time = None
        print("[Monitor] Torque monitor active.")

        while not self._stop_monitor.is_set():
            try:
                current_torque = abs(self.motor.get_torque())

                if current_torque > self.max_torque_limit:
                    if stall_start_time is None:
                        stall_start_time = time.time()

                    # 持续时间超过 150ms 则判定为堵转
                    if (time.time() - stall_start_time) > 0.150:
                        self._handle_emergency_stop("STALL_DETECTED")
                else:
                    stall_start_time = None

            except Exception as e:
                print(f"[Monitor] Comm Error: {e}")

            time.sleep(0.02)  # 50Hz 采样速率

    def _handle_emergency_stop(self, reason: str):
        """紧急停止：快速停机并锁定抱闸"""
        self.is_stalled = True
        self.motor.quick_stop()
        print(f"!!! EMERGENCY STOP: {reason} !!!")
        # 此处可根据硬件添加额外的物理 GPIO 控制抱闸

    def setup(self, max_torque=800):
        """初始化驱动器并开启监控"""
        print("[System] Initializing Machine...")
        self.max_torque_limit = max_torque
        self.motor.enable()

        # 开启后台监控
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(target=self._torque_monitor_loop, daemon=True)
        self._monitor_thread.start()

    def home_platform(self, strategy: str = "limit_switch"):
        """
        升降平台回零：平台先下降到底部，再将该位置设为坐标原点。

        strategy:
          "limit_switch"  — 向下运动直到触碰底部限位开关（需接 DI），推荐正式使用
          "mechanical"    — 向下运动直到堵转（无需限位开关，适合无传感器场景）
          "current_pos"   — 直接将当前位置设为原点，平台不动，仅用于调试
        """
        if self.is_stalled:
            return

        # 清除可能残留的故障（如上次运动被强制中断）
        if self.motor.is_fault():
            print("[System] Clearing fault before homing ...")
            self.motor.reset_fault()
            self.motor.enable()

        # 下降速度（慢速，安全优先）
        fast_speed  = self.motor.rpm_to_puls(30)   # 高速搜索：30 r/min
        slow_speed  = self.motor.rpm_to_puls(5)    # 低速确认：5 r/min
        accel       = 50_000                        # Pul/s²

        if strategy == "limit_switch":
            # 方式 28：向负方向搜索限位开关，到达开关位置停止并清零
            print("[System] Homing: descending to bottom limit switch ...")
            self.motor.set_homing_mode(
                method=HomingMethod.HOME_SWITCH_NEG,
                fast_speed=fast_speed,
                slow_speed=slow_speed,
                acceleration=accel,
                home_offset=0,
            )
        elif strategy == "mechanical":
            # 方式 38：向负方向低速顶到机械限位，堵转后清零（无需传感器）
            print("[System] Homing: descending to mechanical bottom stop ...")
            self.motor.set_homing_mode(
                method=HomingMethod.MECHANICAL_NEG,
                fast_speed=slow_speed,   # 机械限位全程低速，保护机构
                slow_speed=slow_speed,
                acceleration=accel,
                home_offset=0,
            )
        else:  # current_pos
            # 方式 35：当前位置即原点，平台不动，仅调试用
            print("[System] Homing: setting current position as origin (debug only) ...")
            self.motor.set_homing_mode(
                method=HomingMethod.CURRENT_POSITION,
                home_offset=0,
            )

        try:
            self.motor.home(wait=True, timeout=30.0)
            print("[System] Homing complete. Bottom position set as origin.")
        except RuntimeError as e:
            print(f"[Error] Homing failed: {e}")
            if strategy == "limit_switch":
                print("[Hint]  Check DI wiring for bottom limit switch.")
            self.is_stalled = True

    def move_to_height(self, target_mm: float, speed_mm_s: float = 10.0):
        """移动到指定高度"""
        if self.is_stalled:
            print("[Error] System is locked due to fault.")
            return

        target_pulses = self.mm_to_pulses(target_mm)
        # 速度转换：mm/s -> Pul/s
        speed_pul_s = (speed_mm_s / self.LEADSCREW_LEAD) * self.GEARBOX_RATIO * self.PULSES_PER_REV

        print(f"[Move] Target: {target_mm}mm ({target_pulses} pulses)")
        self.motor.set_position_mode(profile_velocity=int(speed_pul_s))
        self.motor.move_absolute(target_pulses)


# --- 使用示例 ---
if __name__ == "__main__":
    with CANopenNode(channel="can0", node_id=1) as node:
        controller = LiftingPlatformController(node)

        # 1. 启动并设置 1.2倍 额定力矩为堵转阈值
        controller.setup(max_torque=1200)

        # 2. 回零建立坐标系：平台下降到底部
        # 调试时用 "current_pos"，接好限位开关后改为 "limit_switch"
        controller.home_platform(strategy="current_pos")

        # 3. 升至 target_mm 高度
        controller.move_to_height(300.0, speed_mm_s=20.0)

        # 4. 等待两秒
        time.sleep(2.0)

        # 5. 返回零点
        controller.move_to_height(0.0, speed_mm_s=20.0)

        # 6. 保持运行直到监控退出
        try:
            while not controller.is_stalled:
                time.sleep(1)
        except KeyboardInterrupt:
            controller.motor.shutdown()