/**
 * @file    test.c
 * @brief   Hardware self-test — exercises every motor and servo channel.
 */
#include "test.h"
#include "motor.h"
#include "servo.h"
#include "uart_comm.h"
#include "stm32g4xx_hal.h"

/* ═══════════════════════════════════════════════════════════════════════════ */

static const char motor_name[] = "ABCDEFGH";

void Test_RunAll(void)
{
    UART_Printf("\r\n========== FULL TEST START ==========\r\n");
    Test_Motors();
    Test_Servos();
    UART_Printf("========== FULL TEST END   ==========\r\n\r\n");
}

/* ── Motor test ────────────────────────────────────────────────────────── */

void Test_SingleMotor(uint8_t idx)
{
    if (idx >= MOTOR_COUNT) return;
    Motor_ID_t id = (Motor_ID_t)idx;

    UART_Printf("  Motor %c : FWD 30%%...", motor_name[idx]);
    Motor_SetSpeed(id, 30);
    HAL_Delay(600);

    Motor_Coast(id);
    HAL_Delay(150);

    UART_Printf(" REV 30%%...");
    Motor_SetSpeed(id, -30);
    HAL_Delay(600);

    UART_Printf(" BRAKE...");
    Motor_Brake(id);
    HAL_Delay(200);

    Motor_Coast(id);
    UART_Printf(" OK\r\n");
    HAL_Delay(200);
}

void Test_Motors(void)
{
    UART_Printf("\r\n--- Motor Test (A-H, 30%% FWD/REV) ---\r\n");
    for (uint8_t i = 0; i < MOTOR_COUNT; i++) {
        Test_SingleMotor(i);
    }
    Motor_StopAll();
    UART_Printf("--- Motor Test Complete ---\r\n");
}

/* ── Servo test ────────────────────────────────────────────────────────── */

void Test_SingleServo(uint8_t idx)
{
    if (idx >= SERVO_COUNT) return;
    Servo_ID_t id = (Servo_ID_t)idx;

    UART_Printf("  Servo %d : 0deg...", idx + 1);
    Servo_SetAngle(id, 0);
    HAL_Delay(500);

    UART_Printf(" 90deg...");
    Servo_SetAngle(id, 90);
    HAL_Delay(500);

    UART_Printf(" 180deg...");
    Servo_SetAngle(id, 180);
    HAL_Delay(500);

    Servo_SetAngle(id, 90);
    UART_Printf(" centre. OK\r\n");
    HAL_Delay(300);
}

void Test_Servos(void)
{
    UART_Printf("\r\n--- Servo Test (1-4, sweep 0/90/180) ---\r\n");
    for (uint8_t i = 0; i < SERVO_COUNT; i++) {
        Test_SingleServo(i);
    }
    UART_Printf("--- Servo Test Complete ---\r\n");
}
