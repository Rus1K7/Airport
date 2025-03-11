import uvicorn
from fastapi import FastAPI, HTTPException, Body, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, List
import uuid
import random
import requests
import logging
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from tabulate import tabulate

# Настройка шаблонов
templates = Jinja2Templates(directory="templates")

# Настройка логгера с уровнем DEBUG для диагностики
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("PassengersAPI")

# URL модулей
TABLO_API_URL = "http://172.20.10.2:8003/v1/flights"  # Табло
TICKETS_API_URL = "http://172.20.10.2:8005/v1/tickets/buy"  # Касса
CHECKIN_API_URL = "http://172.20.10.2:8006/v1/checkin"  # Check-In


# Модель данных для билета
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
    state: str = "CameToAirport"  # Возможные состояния: CameToAirport, GotTicket, CheckedIn, ReadyForBus, OnBus, Boarded
    isVIP: bool

    class Config:
        json_encoders = {"datetime": lambda v: v.isoformat()}


# База данных пассажиров (in-memory)
passengers_db = {}

# Константы
MENU_TYPES = ["meat", "chicken", "fish", "vegan"]
NAMES = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace", "Henry"]
VALID_STATES = ["CameToAirport", "GotTicket", "CheckedIn", "ReadyForBus", "OnBus", "Boarded"]


# Функция для получения доступных рейсов с Табло
def get_available_flights() -> List[dict]:
    try:
        response = requests.get(TABLO_API_URL)
        response.raise_for_status()
        flights = response.json()
        available = [f for f in flights if
                     f["status"] in ["Scheduled"]]
        logger.debug(f"Получено {len(available)} доступных рейсов с Табло")
        return available
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе к Табло: {e}")
        return []


# Функция для проверки рейса
def check_flight(flightId: str) -> dict:
    try:
        response = requests.get(f"{TABLO_API_URL}/{flightId}")
        response.raise_for_status()
        flight_data = response.json()
        logger.debug(f"Рейс {flightId} проверен: статус {flight_data['status']}")
        return flight_data
    except requests.RequestException as e:
        logger.error(f"Не удалось проверить рейс {flightId}: {e}")
        raise HTTPException(status_code=503, detail="Ошибка при проверке рейса")


# Функция для вывода таблицы пассажиров
def print_passengers_table():
    passengers = list(passengers_db.values())
    if not passengers:
        logger.info("Таблица пассажиров пуста")
        print("\n--- Таблица пассажиров ---\nНет пассажиров\n-------------------------")
        return

    table_data = []
    for p in passengers:
        flight_time = p.ticket.flightDepartureTime if p.ticket and p.ticket.flightDepartureTime else "N/A"
        if flight_time != "N/A":
            flight_time = flight_time.split("T")[1][:5]  # Например, "09:00"
        table_data.append([flight_time, p.flightId, p.id, p.name, p.state, p.baggageWeight, p.menuType, str(p.isVIP),
                           p.ticket.ticketId if p.ticket else "Нет"])

    table_data.sort(key=lambda x: (x[0] if x[0] != "N/A" else "ZZ:ZZ", x[1]))
    headers = ["Время", "Рейс", "ID", "Имя", "Статус", "Вес багажа", "Тип питания", "VIP", "Билет"]
    logger.info("Вывод таблицы пассажиров")
    print(
        f"\n--- Таблица пассажиров ---\n{tabulate(table_data, headers=headers, tablefmt='grid')}\n-------------------------")


# Функция создания пассажира (общая для автоматической и ручной генерации)
def create_passenger_instance(name: str, flightId: str, baggageWeight: int, menuType: str, isVIP: bool) -> Passenger:
    passenger_id = str(uuid.uuid4())
    flight_data = check_flight(flightId)
    if flight_data["status"] in ["Departed", "Arrived", "Cancelled"]:
        logger.error(f"Рейс {flightId} недоступен (статус: {flight_data['status']})")
        raise HTTPException(status_code=400, detail="Рейс недоступен")

    passenger = Passenger(id=passenger_id, name=name, flightId=flightId, baggageWeight=baggageWeight, menuType=menuType,
                          ticket=None, state="CameToAirport", isVIP=isVIP)

    # Покупка билета
    try:
        ticket_response = requests.post(TICKETS_API_URL, json={
            "passengerId": passenger_id, "passengerName": name, "flightId": flightId, "isVIP": isVIP,
            "menuType": menuType, "baggageWeight": baggageWeight
        })
        ticket_response.raise_for_status()
        ticket_data = ticket_response.json()
        passenger.ticket = Ticket(**ticket_data)
        passenger.state = "GotTicket"
        logger.info(f"Пассажир {name} купил билет {ticket_data['ticketId']}")
    except requests.RequestException as e:
        logger.error(f"Ошибка при покупке билета для {name}: {e}")
        return passenger  # Возвращаем без билета, чтобы не потерять пассажира

    # Автоматическая регистрация
    if flight_data["status"] in ["RegistrationOpen", "RegistrationClosed"]:
        try:
            logger.debug(f"Отправка POST-запроса на регистрацию: {flightId}, {passenger_id}, {ticket_data['ticketId']}")
            checkin_response = requests.post(f"{CHECKIN_API_URL}/start", json={
                "flightId": flightId, "passengerId": passenger_id, "ticketId": ticket_data["ticketId"]
            })
            checkin_response.raise_for_status()
            checkin_data = checkin_response.json()
            passenger.state = "CheckedIn"
            logger.info(f"Пассажир {name} зарегистрирован, checkInId: {checkin_data['checkInId']}")
        except requests.RequestException as e:
            logger.error(f"Ошибка при регистрации {name}: {e}")

    passengers_db[passenger_id] = passenger
    logger.info(f"Пассажир {name} (ID: {passenger_id}) создан с рейсом {flightId}")
    print(
        f"\n--- Новый пассажир ---\nID: {passenger_id}\nИмя: {name}\nРейс: {flightId}\nВес багажа: {baggageWeight}\nТип питания: {menuType}\nСтатус: {passenger.state}\nVIP: {isVIP}\nБилет: {passenger.ticket.ticketId if passenger.ticket else 'Нет'}\n---------------------")
    return passenger


# Автоматическая генерация пассажира
def generate_passenger():
    name = random.choice(NAMES)
    baggageWeight = random.randint(0, 20)
    menuType = random.choice(MENU_TYPES)
    isVIP = random.random() < 0.2
    available_flights = get_available_flights()
    if not available_flights:
        logger.error("Нет доступных рейсов для генерации пассажира")
        return
    flightId = random.choice(available_flights)["flightId"]
    logger.debug(f"Генерация пассажира {name} для рейса {flightId}")
    create_passenger_instance(name, flightId, baggageWeight, menuType, isVIP)


# Автоматическая регистрация пассажиров
def auto_checkin_passengers():
    logger.debug(f"Запуск автоматической регистрации, пассажиров в базе: {len(passengers_db)}")
    for passenger in passengers_db.values():
        if passenger.state == "GotTicket":
            try:
                flight_data = check_flight(passenger.flightId)
                if flight_data["status"] not in ["RegistrationOpen", "RegistrationClosed"]:
                    logger.debug(f"Регистрация для {passenger.name} невозможна, статус рейса: {flight_data['status']}")
                    continue
                logger.debug(
                    f"Регистрация {passenger.name}: {passenger.flightId}, {passenger.id}, {passenger.ticket.ticketId}")
                checkin_response = requests.post(f"{CHECKIN_API_URL}/start", json={
                    "flightId": passenger.flightId, "passengerId": passenger.id, "ticketId": passenger.ticket.ticketId
                })
                checkin_response.raise_for_status()
                checkin_data = checkin_response.json()
                passenger.state = "CheckedIn"
                logger.info(f"Автоматическая регистрация {passenger.name}, checkInId: {checkin_data['checkInId']}")
            except requests.RequestException as e:
                logger.error(f"Ошибка автоматической регистрации {passenger.name}: {e}")


# Инициализация планировщика
scheduler = BackgroundScheduler()
scheduler.add_job(generate_passenger, 'interval', seconds=2)
scheduler.add_job(print_passengers_table, 'interval', seconds=60)
scheduler.add_job(auto_checkin_passengers, 'interval', seconds=10)


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
def create_passenger(name: Optional[str] = None, flightId: Optional[str] = None, baggageWeight: Optional[int] = 0,
                     menuType: Optional[str] = None, isVIP: Optional[bool] = False):
    name = name or random.choice(NAMES)
    menuType = menuType or random.choice(MENU_TYPES)
    if not flightId:
        available_flights = get_available_flights()
        if not available_flights:
            raise HTTPException(status_code=404, detail="Нет доступных рейсов")
        flightId = random.choice(available_flights)["flightId"]
    logger.debug(f"Ручное создание пассажира {name} для рейса {flightId}")
    return create_passenger_instance(name, flightId, baggageWeight, menuType, isVIP)


# Получение всех пассажиров
@app.get("/v1/passengers", response_model=List[Passenger])
def get_all_passengers():
    passengers = list(passengers_db.values())
    logger.info(f"Запрошен список всех пассажиров: {len(passengers)} записей")
    return passengers


# Получение пассажиров по рейсу
@app.get("/v1/passengers/flight/{flightId}", response_model=List[Passenger])
def get_passengers_by_flight(flightId: str):
    passengers = [p for p in passengers_db.values() if p.flightId == flightId]
    logger.info(f"Запрошены пассажиры рейса {flightId}: найдено {len(passengers)}")
    return passengers


# Получение пассажира по ID
@app.get("/v1/passengers/{passengerId}", response_model=Passenger)
def get_passenger(passengerId: str):
    passenger = passengers_db.get(passengerId)
    if not passenger:
        logger.error(f"Пассажир с ID {passengerId} не найден")
        raise HTTPException(status_code=404, detail="Пассажир не найден")
    logger.info(f"Запрошена информация о пассажире {passenger.name} (ID: {passengerId})")
    return passenger


# Регистрация пассажира
@app.post("/v1/passengers/{passengerId}/checkin", response_model=Passenger)
def checkin_passenger(passengerId: str):
    passenger = passengers_db.get(passengerId)
    if not passenger:
        raise HTTPException(status_code=404, detail="Пассажир не найден")
    if passenger.state != "GotTicket":
        raise HTTPException(status_code=400, detail="Пассажир не может зарегистрироваться")
    try:
        logger.debug(f"Регистрация {passenger.name}: {passenger.flightId}, {passenger.id}, {passenger.ticket.ticketId}")
        checkin_response = requests.post(f"{CHECKIN_API_URL}/start", json={
            "flightId": passenger.flightId, "passengerId": passenger.id, "ticketId": passenger.ticket.ticketId
        })
        checkin_response.raise_for_status()
        checkin_data = checkin_response.json()
        passenger.state = "CheckedIn"
        logger.info(f"Пассажир {passenger.name} зарегистрирован, checkInId: {checkin_data['checkInId']}")
        passenger.state = "ReadyForBus"
        logger.info(f"Пассажир {passenger.name} готов к посадке в автобус")
    except requests.RequestException as e:
        logger.error(f"Ошибка при регистрации {passenger.name}: {e}")
        raise HTTPException(status_code=503, detail="Ошибка при регистрации")
    return passenger


# Обновление состояния
@app.patch("/v1/passengers/{passengerId}/state", response_model=Passenger)
def update_passenger_state(passengerId: str, state: str = Body(..., embed=True)):
    passenger = passengers_db.get(passengerId)
    if not passenger:
        raise HTTPException(status_code=404, detail="Пассажир не найден")
    if state not in VALID_STATES:
        raise HTTPException(status_code=400, detail="Неверное состояние")
    passenger.state = state
    logger.info(f"Состояние пассажира {passenger.name} обновлено на {state}")
    return passenger


# UI: Главная страница
@app.get("/ui", response_class=HTMLResponse)
async def ui_home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "passengers": list(passengers_db.values())})


# UI: Установка VIP-статуса
@app.post("/ui/set_vip", response_class=RedirectResponse)
async def ui_set_vip(passenger_id: str = Form(...)):
    passenger = passengers_db.get(passenger_id)
    if not passenger:
        raise HTTPException(status_code=404, detail="Пассажир не найден")
    passenger.isVIP = True
    logger.info(f"У пассажира {passenger.name} (ID: {passenger.id}) установлен VIP-статус")
    return RedirectResponse(url="/ui", status_code=303)


# UI: Подделка билета
@app.post("/ui/fake_ticket", response_class=RedirectResponse)
async def ui_fake_ticket(passenger_id: str = Form(...)):
    passenger = passengers_db.get(passenger_id)
    if not passenger or not passenger.ticket:
        raise HTTPException(status_code=400, detail="Пассажир не найден или нет билета")
    old_ticket = passenger.ticket.ticketId
    new_ticket_data = passenger.ticket.dict()
    new_ticket_data["ticketId"] = str(uuid.uuid4())
    passenger.ticket = Ticket(**new_ticket_data)
    logger.info(f"Билет пассажира {passenger.name} изменён с {old_ticket} на {new_ticket_data['ticketId']}")
    return RedirectResponse(url="/ui", status_code=303)


if __name__ == "__main__":
    uvicorn.run("passengers_api:app", host="172.20.10.2", port=8004, reload=True)