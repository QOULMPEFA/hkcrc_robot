/**
 * @file    servo.h
 * @brief   PWM servo control (4 channels on TIM8, PC6–PC9).
 */
#ifndef SERVO_H
#define SERVO_H

#ifdef __cplusplus
extern "C" {
#endif

#include "stm32g4xx_hal.h"
#include <stdint.h>

typedef enum {
    SERVO_1 = 0,   /* TIM8 CH1  PC6 */
    SERVO_2,       /* TIM8 CH2  PC7 */
    SERVO_3,       /* TIM8 CH3  PC8 */
    SERVO_4,       /* TIM8 CH4  PC9 */
    SERVO_COUNT
} Servo_ID_t;

/* 170 MHz / 170 = 1 MHz tick → 20 000 ticks = 20 ms = 50 Hz */
#define SERVO_TIM_PRESCALER    (170U - 1U)
#define SERVO_PWM_PERIOD       (20000U - 1U)

#define SERVO_PULSE_MIN_US     500U    /* 0°   */
#define SERVO_PULSE_MAX_US     2500U   /* 180° */

void Servo_Init(void);

/**
 * @brief  Set servo angle.
 * @param  servo   SERVO_1 … SERVO_4
 * @param  angle   0 – 180 (degrees, integer)
 */
void Servo_SetAngle(Servo_ID_t servo, uint16_t angle);

/**
 * @brief  Set servo pulse width directly.
 * @param  pulse_us  500 – 2500 µs
 */
void Servo_SetPulseUs(Servo_ID_t servo, uint16_t pulse_us);

#ifdef __cplusplus
}
#endif

#endif /* SERVO_H */
