import sys
import subprocess
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Main")

modules = [
    ("flights_api:app", 8003),
    ("time_control:app", 8005),
]

processes = []

try:
    for module, port in modules:
        logger.info(f"Запуск {module} на порту {port}")
        process = subprocess.Popen([sys.executable, module],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        processes.append(process)

    while True:
        time.sleep(1)

except KeyboardInterrupt:
    logger.info("Остановка всех процессов...")
    for process in processes:
        process.terminate()
    logger.info("Все процессы завершены.")
