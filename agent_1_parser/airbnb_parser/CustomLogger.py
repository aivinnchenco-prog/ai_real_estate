import os
import sys
from loguru import logger
from datetime import datetime


if getattr(sys, 'frozen', False):
    applicationExePath = os.path.dirname(sys.executable)
elif __file__:
    applicationExePath = os.path.dirname(__file__)

# if getattr(sys, 'frozen', False):
#     applicationExePath = sys._MEIPASS
# else:
#     applicationExePath = os.path.dirname(__file__)

logs_folder = os.path.join(applicationExePath, 'Logs')

# with open('H:\\Temp\\Info.txt', 'w', encoding='utf-8') as f:
#     f.write(applicationExePath + '\n')
#     f.write(logs_folder + '\n')

if not os.path.exists(logs_folder):
    os.makedirs(logs_folder)

format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{message}</level>"
logger.configure(
    handlers=[{"sink": sys.stdout, "format": format, 'level': "INFO"}])
logger.add(
    f"{logs_folder}/{datetime.now().strftime('%Y-%m-%d')}.log", format=format, level="DEBUG")

