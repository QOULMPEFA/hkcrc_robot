/**
 * @file    test.h
 * @brief   Hardware self-test routines for motors and servos.
 */
#ifndef TEST_H
#define TEST_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/** Run all tests (motors + servos). */
void Test_RunAll(void);

/** Test every motor in sequence. */
void Test_Motors(void);

/** Test every servo in sequence. */
void Test_Servos(void);

/** Test a single motor by index (0-7). */
void Test_SingleMotor(uint8_t idx);

/** Test a single servo by index (0-3). */
void Test_SingleServo(uint8_t idx);

#ifdef __cplusplus
}
#endif

#endif /* TEST_H */
