import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import uuid
import random
import requests
import logging
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from tabulate import tabulate

# Настройка логгера
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("PassengersAPI")

# URL модуля "Табло" и "Кассы" с новыми IP-адресами
TABLO_API_URL = "http://172.20.10.2:8003/v1/flights"  # Табло на 192.168.32.3:8003
TICKETS_API_URL = "http://172.20.10.2:8005/v1/tickets/buy"  # Касса на 192.168.32.5:8005

# Модель данных для билета (согласована с tickets_api.py)
class Ticket(BaseModel):
    ticketId: str
    flightId: str
    passengerId: str
    passengerName: str
    isVIP: bool
    menuType: str
    baggageWeight: int
    status: str
    createdAt: str
    gate: Optional[str] = None
    seatNumber: Optional[str] = None
    flightDepartureTime: Optional[str] = None
    fromCity: Optional[str] = None
    toCity: Optional[str] = None

    class Config:
        json_encoders = {"datetime": lambda v: v.isoformat()}

# Модель данных для пассажира
class Passenger(BaseModel):
    id: str
    name: str
    flightId: str
    baggageWeight: int
    menuType: str
    ticket: Optional[Ticket] = None
    state: str = "CameToAirport"
    isVIP: bool

    class Config:
        json_encoders = {"datetime": lambda v: v.isoformat()}

# База данных пассажиров (in-memory)
passengers_db = {}

# Список возможных типов питания и имён
MENU_TYPES = ["meat", "chicken", "fish", "vegan"]
NAMES = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace", "Henry"]

# Функция для получения списка рейсов с "Табло"
def get_available_flights() -> List[dict]:
    try:
        response = requests.get(TABLO_API_URL)
        response.raise_for_status()
        flights = response.json()
        available = [flight for flight in flights if flight["status"] not in ["Departed", "Arrived", "Cancelled"]]
        logger.info(f"Получено {len(available)} доступных рейсов с Табло")
        return available
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе к Табло: {e}")
        return []

# Функция для вывода таблицы пассажиров
def print_passengers_table():
    passengers = list(passengers_db.values())
    if not passengers:
        print("\n--- Таблица пассажиров ---")
        print("Нет пассажиров")
        print("-------------------------\n")
        return

    table_data = []
    for passenger in passengers:
        flight_time = passenger.ticket.flightDepartureTime if passenger.ticket and passenger.ticket.flightDepartureTime else "N/A"
        if flight_time != "N/A":
            flight_time = flight_time.split("T")[1][:5]  # Например, "09:00" из "2025-03-15T09:00:00"
        table_data.append([
            flight_time,
            passenger.flightId,
            passenger.id,
            passenger.name,
            passenger.state,
            passenger.baggageWeight,
            passenger.menuType,
            str(passenger.isVIP),
            passenger.ticket.ticketId if passenger.ticket else "Нет"
        ])

    table_data.sort(key=lambda x: (x[0] if x[0] != "N/A" else "ZZ:ZZ", x[1]))
    headers = ["Время", "Рейс", "ID", "Имя", "Статус", "Вес багажа", "Тип питания", "VIP", "Билет"]
    print("\n--- Таблица пассажиров ---")
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    print("-------------------------\n")

# Функция автоматической генерации пассажира
def generate_passenger():
    name = random.choice(NAMES)
    baggageWeight = random.randint(0, 20)
    menuType = random.choice(MENU_TYPES)
    isVIP = random.random() < 0.2

    available_flights = get_available_flights()
    if not available_flights:
        logger.error("Нет доступных рейсов для генерации пассажира")
        return

    flight = random.choice(available_flights)
    flightId = flight["flightId"]
    logger.info(f"Сгенерирован пассажир {name}, выбрал рейс: {flightId}")

    try:
        response = requests.get(f"{TABLO_API_URL}/{flightId}")
        response.raise_for_status()
        flight_data = response.json()
        if flight_data["status"] in ["Departed", "Arrived", "Cancelled"]:
            logger.error(f"Рейс {flightId} недоступен (статус: {flight_data['status']})")
            return
        logger.info(f"Рейс {flightId} проверен: статус {flight_data['status']}")
    except requests.RequestException:
        logger.error(f"Не удалось проверить рейс {flightId}")
        return

    passenger_id = str(uuid.uuid4())
    passenger = Passenger(
        id=passenger_id,
        name=name,
        flightId=flightId,
        baggageWeight=baggageWeight,
        menuType=menuType,
        ticket=None,
        state="CameToAirport",
        isVIP=isVIP
    )

    # Покупка билета
    try:
        ticket_response = requests.post(
            TICKETS_API_URL,
            json={
                "passengerId": passenger_id,
                "passengerName": name,
                "flightId": flightId,
                "isVIP": isVIP,
                "menuType": menuType,
                "baggageWeight": baggageWeight
            }
        )
        ticket_response.raise_for_status()
        ticket_data = ticket_response.json()
        passenger.ticket = Ticket(**ticket_data)
        passenger.state = "GotTicket"
        logger.info(f"Пассажир {name} купил билет {ticket_data['ticketId']}")
    except requests.RequestException as e:
        logger.error(f"Ошибка при покупке билета для {name}: {e}")

    passengers_db[passenger_id] = passenger
    logger.info(f"Пассажир {name} (ID: {passenger_id}) успешно создан с рейсом {flightId}")
    print(f"\n--- Новый пассажир ---")
    print(f"ID: {passenger_id}")
    print(f"Имя: {name}")
    print(f"Рейс: {flightId}")
    print(f"Вес багажа: {baggageWeight}")
    print(f"Тип питания: {menuType}")
    print(f"Статус: {passenger.state}")
    print(f"VIP: {isVIP}")
    print(f"Билет: {passenger.ticket.ticketId if passenger.ticket else 'Нет'}")
    print("---------------------\n")

# Инициализация планировщика
scheduler = BackgroundScheduler()
scheduler.add_job(generate_passenger, 'interval', seconds=5)
scheduler.add_job(print_passengers_table, 'interval', seconds=60)

# Запуск приложения
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск модуля Пассажиры")
    scheduler.start()
    yield
    scheduler.shutdown()
    logger.info("Остановка модуля Пассажиры")

app = FastAPI(title="Passengers Module", lifespan=lifespan)

# Создание пассажира вручную
@app.post("/v1/passengers", response_model=Passenger, status_code=201)
def create_passenger(
    name: Optional[str] = None,
    flightId: Optional[str] = None,
    baggageWeight: Optional[int] = 0,
    menuType: Optional[str] = None,
    isVIP: Optional[bool] = False
):
    if not name:
        name = random.choice(NAMES)
        logger.info(f"Сгенерировано случайное имя: {name}")

    if not flightId:
        available_flights = get_available_flights()
        if not available_flights:
            logger.error("Нет доступных рейсов для выбора")
            raise HTTPException(status_code=404, detail="Нет доступных рейсов")
        flight = random.choice(available_flights)
        flightId = flight["flightId"]
        logger.info(f"Пассажир {name} выбрал случайный рейс: {flightId}")
    else:
        logger.info(f"Пассажир {name} указал рейс: {flightId}")

    try:
        response = requests.get(f"{TABLO_API_URL}/{flightId}")
        response.raise_for_status()
        flight_data = response.json()
        if flight_data["status"] in ["Departed", "Arrived", "Cancelled"]:
            logger.error(f"Рейс {flightId} недоступен для регистрации (статус: {flight_data['status']})")
            raise HTTPException(status_code=400, detail="Выбранный рейс недоступен для регистрации")
        logger.info(f"Рейс {flightId} проверен: статус {flight_data['status']}")
    except requests.RequestException:
        logger.error(f"Не удалось проверить рейс {flightId}")
        raise HTTPException(status_code=503, detail="Ошибка при проверки рейса")

    if not menuType:
        menuType = random.choice(MENU_TYPES)
        logger.info(f"Сгенерирован случайный тип питания для {name}: {menuType}")

    passenger_id = str(uuid.uuid4())
    passenger = Passenger(
        id=passenger_id,
        name=name,
        flightId=flightId,
        baggageWeight=baggageWeight,
        menuType=menuType,
        ticket=None,
        state="CameToAirport",
        isVIP=isVIP
    )
    passengers_db[passenger_id] = passenger
    logger.info(f"Пассажир {name} (ID: {passenger_id}) успешно создан с рейсом {flightId}")
    print(f"\n--- Новый пассажир ---")
    print(f"ID: {passenger_id}")
    print(f"Имя: {name}")
    print(f"Рейс: {flightId}")
    print(f"Вес багажа: {baggageWeight}")
    print(f"Тип питания: {menuType}")
    print(f"Статус: {passenger.state}")
    print(f"VIP: {isVIP}")
    print("---------------------\n")
    return passenger

@app.get("/v1/passengers", response_model=List[Passenger])
def get_all_passengers():
    passengers = list(passengers_db.values())
    logger.info(f"Запрошен список всех пассажиров: {len(passengers)} записей")
    return passengers

# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
# получение всех пассажиров по конкретному рейсу
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

@app.get("/v1/passengers/{passengerId}", response_model=Passenger)
def get_passenger(passengerId: str):
    passenger = passengers_db.get(passengerId)
    if not passenger:
        logger.error(f"Пассажир с ID {passengerId} не найден")
        raise HTTPException(status_code=404, detail="Пассажир не найден")
    logger.info(f"Запрошена информация о пассажире {passenger.name} (ID: {passengerId})")
    print(f"\n--- Информация о пассажире ---")
    print(f"ID: {passenger.id}")
    print(f"Имя: {passenger.name}")
    print(f"Рейс: {passenger.flightId}")
    print(f"Вес багажа: {passenger.baggageWeight}")
    print(f"Тип питания: {passenger.menuType}")
    print(f"Статус: {passenger.state}")
    print(f"VIP: {passenger.isVIP}")
    print("---------------------\n")
    return passenger

if __name__ == "__main__":
    uvicorn.run("passengers_api:app", host="172.20.10.2", port=8004, reload=True)  # Хост для пассажиров