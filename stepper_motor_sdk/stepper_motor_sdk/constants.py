"""CiA 402 / CANopen object dictionary constants and state machine definitions."""

# ─── CiA 402 Object Dictionary ───────────────────────────────────────────────

class OD:
    """CiA 402 object dictionary indices."""
    DEVICE_TYPE          = 0x1000
    ERROR_REGISTER       = 0x1001
    SYNC_COB_ID          = 0x1005
    SYNC_CYCLE_PERIOD    = 0x1006
    HEARTBEAT_PRODUCER   = 0x1017
    SDO_SERVER_RX        = (0x1200, 1)
    SDO_SERVER_TX        = (0x1200, 2)

    # CiA 402 drive / motion
    CONTROLWORD          = 0x6040
    STATUSWORD           = 0x6041
    QUICK_STOP_CODE      = 0x605A
    MODES_OF_OPERATION   = 0x6060
    MODES_OF_OPERATION_DISPLAY = 0x6061
    POSITION_DEMAND      = 0x60FC
    POSITION_ACTUAL_ENC  = 0x6063
    POSITION_ACTUAL      = 0x6064   # Pul
    VELOCITY_DEMAND      = 0x606B
    VELOCITY_ACTUAL      = 0x606C   # Pul/s
    TARGET_TORQUE        = 0x6071
    TORQUE_DEMAND        = 0x6074
    TORQUE_ACTUAL        = 0x6077
    TARGET_POSITION      = 0x607A   # Pul
    HOME_OFFSET          = 0x607C
    POLARITY             = 0x607E
    MAX_PROFILE_VELOCITY = 0x607F
    PROFILE_VELOCITY     = 0x6081
    PROFILE_ACCELERATION = 0x6083
    PROFILE_DECELERATION = 0x6084
    QUICK_STOP_DECEL     = 0x6085
    HOMING_METHOD        = 0x6098
    HOMING_SPEED         = 0x6099   # sub1=fast, sub2=slow
    HOMING_ACCELERATION  = 0x609A
    TARGET_VELOCITY      = 0x60FF   # Pul/s  (PV mode)
    SUPPORTED_MODES      = 0x6502


# ─── CiA 402 Modes of Operation ──────────────────────────────────────────────

class OperationMode:
    NO_MODE  = 0
    PP       = 1    # Profile Position
    PV       = 3    # Profile Velocity
    PT       = 4    # Profile Torque
    HM       = 6    # Homing
    CSP      = 8    # Cyclic Synchronous Position


# ─── Controlword (0x6040) bit masks ──────────────────────────────────────────

class Controlword:
    SWITCH_ON           = 0x0001    # bit 0
    ENABLE_VOLTAGE      = 0x0002    # bit 1
    QUICK_STOP          = 0x0004    # bit 2  (0=active)
    ENABLE_OPERATION    = 0x0008    # bit 3
    NEW_SETPOINT        = 0x0010    # bit 4  PP: rising-edge triggers move
    CHANGE_ON_SETPOINT  = 0x0020    # bit 5  PP
    ABS_REL             = 0x0040    # bit 6  PP: 0=abs, 1=rel
    FAULT_RESET         = 0x0080    # bit 7  rising-edge resets fault
    HALT                = 0x0100    # bit 8
    # HM start: bit 4 rising-edge

    # Canonical state-machine commands (CiA 402 §3.4)
    CMD_SHUTDOWN         = 0x0006   # bits: !switch_on, enable_voltage, quick_stop
    CMD_SWITCH_ON        = 0x0007   # bits: switch_on, enable_voltage, quick_stop
    CMD_ENABLE_OPERATION = 0x000F   # bits: switch_on, ev, qs, enable_op
    CMD_DISABLE_VOLTAGE  = 0x0000
    CMD_QUICK_STOP       = 0x0002
    CMD_FAULT_RESET      = 0x0080


# ─── Statusword (0x6041) bit masks ───────────────────────────────────────────

class Statusword:
    READY_TO_SWITCH_ON  = 0x0001    # bit 0
    SWITCHED_ON         = 0x0002    # bit 1
    OPERATION_ENABLED   = 0x0004    # bit 2
    FAULT               = 0x0008    # bit 3
    VOLTAGE_ENABLED     = 0x0010    # bit 4
    QUICK_STOP          = 0x0020    # bit 5  (0 = quick-stop active)
    SWITCH_ON_DISABLED  = 0x0040    # bit 6
    WARNING             = 0x0080    # bit 7
    REMOTE              = 0x0200    # bit 9
    TARGET_REACHED      = 0x0400    # bit 10
    HOME_ATTAINED       = 0x8000    # bit 15

    # State masks / values  (CiA 402 Table 3)
    MASK_STATE           = 0x006F
    STATE_NOT_READY      = 0x0000
    STATE_SWITCH_ON_DISABLED = 0x0040
    STATE_READY_TO_SWITCH_ON = 0x0021
    STATE_SWITCHED_ON    = 0x0023
    STATE_OPERATION_ENABLED = 0x0027
    STATE_QUICK_STOP_ACTIVE = 0x0007
    STATE_FAULT_REACTION = 0x000F
    STATE_FAULT          = 0x0008


# ─── Homing methods ──────────────────────────────────────────────────────────

class HomingMethod:
    CURRENT_POSITION     = 35   # set current as home (no movement)
    LIMIT_SWITCH_NEG     = 17
    LIMIT_SWITCH_POS     = 18
    HOME_SWITCH_POS      = 24
    HOME_SWITCH_NEG      = 28
    HOME_Z_POS           = 34
    HOME_Z_NEG           = 33
    MECHANICAL_POS       = 37
    MECHANICAL_NEG       = 38


# ─── CANopen function codes / COB-ID ─────────────────────────────────────────

class COBType:
    NMT          = 0x000
    SYNC         = 0x080
    EMCY         = 0x080    # offset + node_id
    TPDO1        = 0x180
    RPDO1        = 0x200
    TPDO2        = 0x280
    RPDO2        = 0x300
    TPDO3        = 0x380
    RPDO3        = 0x400
    TPDO4        = 0x480
    RPDO4        = 0x500
    SDO_TX       = 0x580    # response from node (server -> client)
    SDO_RX       = 0x600    # request to node   (client -> server)
    HEARTBEAT    = 0x700


# ─── SDO command specifiers ───────────────────────────────────────────────────

class SDOCommand:
    # client → server (download / write)
    DOWNLOAD_4BYTE   = 0x23
    DOWNLOAD_2BYTE   = 0x2B
    DOWNLOAD_1BYTE   = 0x2F
    # server → client (response)
    DOWNLOAD_RESP    = 0x60
    # client → server (upload / read)
    UPLOAD_REQ       = 0x40
    # server → client (upload response)
    UPLOAD_RESP_4    = 0x43
    UPLOAD_RESP_2    = 0x4B
    UPLOAD_RESP_1    = 0x4F
    ABORT            = 0x80


# ─── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_NODE_ID    = 1
DEFAULT_BITRATE    = 500_000    # 500 kbps
DEFAULT_PULSES_PER_REV = 1000   # AIMotor factory default
SDO_TIMEOUT        = 3.0        # seconds (increased for shared CAN bus with UGV traffic)
