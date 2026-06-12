"""Test hand detection on can0 at 1 Mbps."""
import sys, asyncio
sys.path.insert(0, "/home/yu/robot_code")
from bc_stark_sdk import main_mod as sdk
from common_socketcan import socketcan_open, socketcan_close, socketcan_send_message, socketcan_receive_filtered

async def test():
    sdk.init_logging()
    try:
        client = sdk.init_device_handler(sdk.StarkProtocolType.Can, 1)
        socketcan_open("can0", canfd=False)
        sdk.set_can_tx_callback(lambda sid, cid, data: socketcan_send_message(cid, bytes(data)))
        sdk.set_can_rx_callback(lambda sid, eid, ef: (
            lambda r: (r[0], bytes(r[1])) if r else (0, bytes())
        )(socketcan_receive_filtered(eid, ef)))

        info = await client.get_device_info(0x02)
        desc = info.description if info else "?"
        print(f"HAND FOUND: {desc}")

        status = await client.get_motor_status(0x02)
        print(f"Positions: {[p/1000.0 for p in status.positions]}")

        socketcan_close()
    except Exception as e:
        print(f"HAND NOT FOUND: {e}")

asyncio.run(test())
