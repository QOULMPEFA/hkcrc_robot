/**
 * @file    motor.h
 * @brief   DC Motor control interface for AT8236 H-bridge drivers.
 *          Supports 8 motors (A–H), bidirectional PWM speed control.
 */
#ifndef MOTOR_CTRL_H
#define MOTOR_CTRL_H

#ifdef __cplusplus
extern "C" {
#endif

#include "stm32g4xx_hal.h"
#include <stdint.h>

/* ── Motor identifiers ──────────────────────────────────────────────────── */

typedef enum {
    MOTOR_A = 0,   /* TIM1  CH1/CH2  PC0 /PC1  */
    MOTOR_B,       /* TIM1  CH3/CH4  PC2 /PC3  */
    MOTOR_C,       /* TIM2  CH1/CH2  PA0 /PA1  */
    MOTOR_D,       /* TIM2  CH3/CH4  PA2 /PA3  */
    MOTOR_E,       /* TIM3  CH1/CH2  PA6 /PA7  */
    MOTOR_F,       /* TIM15 CH2/CH1  PB15/PB14 (IN1/IN2 swapped vs channel) */
    MOTOR_G,       /* TIM3  CH3/CH4  PB0 /PB1  */
    MOTOR_H,       /* TIM5  CH1/CH2  PB2 /PC12 */
    MOTOR_COUNT
} Motor_ID_t;

/* ── Configuration constants ────────────────────────────────────────────── */

#define MOTOR_TIM_CLK_HZ    170000000U   /* APB timer clock after PLL       */
#define MOTOR_PWM_FREQ_HZ   20000U       /* 20 kHz – above audible range    */
#define MOTOR_PWM_PERIOD     (MOTOR_TIM_CLK_HZ / MOTOR_PWM_FREQ_HZ - 1U)  /* 8499 */

/* ── Public API ─────────────────────────────────────────────────────────── */

/**
 * @brief  Initialise GPIO, timers, and start PWM channels for all motors.
 */
void Motor_Init(void);

/**
 * @brief  Set speed and direction of a motor.
 * @param  motor  MOTOR_A … MOTOR_H
 * @param  speed  –100 (full reverse) … 0 (coast) … +100 (full forward)
 */
void Motor_SetSpeed(Motor_ID_t motor, int16_t speed);

/** Active brake (both outputs HIGH). */
void Motor_Brake(Motor_ID_t motor);

/** Coast / free-run (both outputs LOW). */
void Motor_Coast(Motor_ID_t motor);

/** Coast all motors. */
void Motor_StopAll(void);

#ifdef __cplusplus
}
#endif

#endif /* MOTOR_CTRL_H */
