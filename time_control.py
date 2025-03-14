import threading
from datetime import datetime, timedelta
import logging

from db import flights_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TimeControl")

simulation_time = datetime(2025, 3, 15, 7, 57)
time_speed_multiplier = 60 * 0.5  # 1 минут игрового времени за 1 секунду реального

def update_simulation_time():
    global simulation_time
    while True:
        simulation_time += timedelta(seconds=time_speed_multiplier)
        logger.info(f"Текущее игровое время: {simulation_time.strftime('%Y-%m-%d %H:%M:%S')}")
        display_flights_in_console()
        threading.Event().wait(1)

def display_flights_in_console():
    print("\n--- Список активных рейсов ---")
    for flight in flights_db.values():
        print(f"Рейс {flight.flightId}: {flight.fromCity} -> {flight.toCity}, "
              f"Время: {flight.scheduledTime.strftime('%H:%M')}, "
              f"Статус: {flight.status}, "
              f"Гейт: {flight.gate or 'Не назначен'}")
    print("-----------------------------\n")

def get_simulation_time():
    return simulation_time

def set_simulation_time(new_time: datetime):
    global simulation_time
    simulation_time = new_time

def set_simulation_speed(new_speed: int):
    global time_speed_multiplier
    time_speed_multiplier = new_speed

def start_time_simulation():
    thread = threading.Thread(target=update_simulation_time, daemon=True)
    thread.start()

if __name__ == "__main__":
    start_time_simulation()