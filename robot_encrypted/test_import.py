"""Test script — verifies encrypted imports work."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("1. Activating PyArmor runtime...")
from pyarmor_runtime_000000 import __pyarmor__
__pyarmor__(__name__, __file__)
print("   OK")

print("2. Importing robot_control.robot...")
from robot_control.robot import Robot
print("   OK")

print("3. Importing robot_control.web_gui.auth...")
from robot_control.web_gui import auth
print("   OK")

print("4. Importing robot_control.web_gui.i18n...")
from robot_control.web_gui import i18n
print("   OK")

print("\nAll imports successful!")
