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

# Настройка логгера с уровнем DEBUG
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("PassengersAPI")

#### !!!!!!!!!!!!!!!!!!!!!!!!!! Ctrl+F проверятть все IP
# URL модулей
TABLO_API_URL = "http://localhost:8003/v1/flights"  # Табло

# TABLO_API_URL = "http://172.20.10.2:8003/v1/flights"
TICKETS_API_URL = "http://172.20.10.2:8005/v1/tickets/buy"
CHECKIN_API_URL = "http://172.20.10.2:8006/v1/checkin"


# Модель данных для билета
class Ticket(BaseModel):
    ticketId: str
    flightId: str
    passengerId: str
    passengerName: str
    isVIP: bool
    menuType: str
    baggageWeight: int
    status: str         # "active", "returned", "fake", ...
    isFake: bool = False  # новое поле, по умолчанию False
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
    ticket: Optional[Ticket] = None  # Оригинальный билет из Ticket Sales
    forgedTicket: Optional[Ticket] = None  # Подделка (отображается у пассажира)
    state: str = "CameToAirport"
    isVIP: bool

    class Config:
        json_encoders = {"datetime": lambda v: v.isoformat()}


# База данных пассажиров и подделанных билетов
passengers_db = {}
faked_tickets = set()

# Модель ответа, содержащая список идентификаторов пассажиров
class PassengersIDs(BaseModel):
    passengers: List[str]

# Константы
MENU_TYPES = ["meat", "chicken", "fish", "vegan"]
NAMES = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace", "Henry"]
VALID_STATES = ["CameToAirport", "GotTicket", "CheckedIn", "ReadyForBus", "OnBus", "Boarded"]


# Функция для получения доступных рейсов
def get_available_flights() -> List[dict]:
    try:
        response = requests.get(TABLO_API_URL)
        response.raise_for_status()
        flights = response.json()
        available = [f for f in flights if f["status"] in ["Scheduled"]]
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

    table_data = [
        [
            p.ticket.flightDepartureTime.split("T")[1][:5] if p.ticket and p.ticket.flightDepartureTime else "N/A",
            p.flightId,
            p.id,
            p.name,
            p.state,
            p.baggageWeight,
            p.menuType,
            str(p.isVIP),
            p.ticket.ticketId if p.ticket else "Нет"
        ]
        for p in passengers
    ]
    table_data.sort(key=lambda x: (x[0] if x[0] != "N/A" else "ZZ:ZZ", x[1]))
    headers = ["Время", "Рейс", "ID", "Имя", "Статус", "Вес багажа", "Тип питания", "VIP", "Билет"]
    logger.info("Вывод таблицы пассажиров")
    print(
        f"\n--- Таблица пассажиров ---\n{tabulate(table_data, headers=headers, tablefmt='grid')}\n-------------------------")

    # Функция создания пассажира


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
        if e.response and e.response.status_code == 409:
            raise HTTPException(status_code=409, detail=f"Конфликт при покупке билета: {e.response.text}")
        return passenger  # Возвращаем без билета

    # Автоматическая регистрация
    if flight_data["status"] in ["RegistrationOpen", "RegistrationClosed"]:
        try:
            logger.debug(f"Регистрация: {flightId}, {passenger_id}, {ticket_data['ticketId']}")
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
    logger.debug(f"Запуск автоматической регистрации, пассажиров: {len(passengers_db)}")
    for passenger in list(passengers_db.values()):  # Используем list для создания копии
        if passenger.state == "GotTicket":
            try:
                flight_data = check_flight(passenger.flightId)
                if flight_data["status"] not in ["RegistrationOpen", "RegistrationClosed"]:
                    logger.debug(f"Регистрация для {passenger.name} невозможна, статус: {flight_data['status']}")
                    continue
                if passenger.forgedTicket:
                    ticket_id_for_checkin = passenger.forgedTicket.ticketId
                    logger.info(f"Пассажир {passenger.name} проходит с подделанным билетом {ticket_id_for_checkin}")
                else:
                    ticket_id_for_checkin = passenger.ticket.ticketId

                logger.debug(
                    f"Регистрация {passenger.name}: {passenger.flightId}, {passenger.id}, {ticket_id_for_checkin}")
                checkin_response = requests.post(f"{CHECKIN_API_URL}/start", json={
                    "flightId": passenger.flightId, "passengerId": passenger.id, "ticketId": ticket_id_for_checkin
                })
                checkin_response.raise_for_status()
                checkin_data = checkin_response.json()
                if passenger.forgedTicket:
                    passenger.state = "CameToAirport"
                    logger.info(
                        f"Пассажир {passenger.name} вернулся в аэропорт, не прошел регистрацию с подделанным билетом")
                else:
                    passenger.state = "CheckedIn"
                    logger.info(f"Автоматическая регистрация {passenger.name}, checkInId: {checkin_data['checkInId']}")

            except requests.RequestException as e:
                logger.error(f"Ошибка автоматической регистрации {passenger.name}: {e}")


import time
from threading import Timer

# Функция для покупки нового билета
def buy_new_ticket(passenger):
    available_flights = get_available_flights()
    if not available_flights:
        logger.info(f"Нет доступных рейсов для пассажира {passenger.name} (ID: {passenger.id})")
        return

    new_flight = random.choice(available_flights)["flightId"]
    logger.info(f"Пассажир {passenger.name} (ID: {passenger.id}) пытается купить новый билет на рейс {new_flight}")

    try:
        ticket_response = requests.post(TICKETS_API_URL, json={
            "passengerId": passenger.id,
            "passengerName": passenger.name,
            "flightId": new_flight,
            "isVIP": passenger.isVIP,
            "menuType": passenger.menuType,
            "baggageWeight": passenger.baggageWeight
        })
        ticket_response.raise_for_status()
        ticket_data = ticket_response.json()

        # Новый билет должен быть настоящим
        passenger.ticket = Ticket(**ticket_data)
        passenger.flightId = new_flight
        passenger.state = "GotTicket"

        # Убираем подделанный билет, если он был
        passenger.forgedTicket = None
        ticket_data["isFake"] = False

        logger.info(f"Пассажир {passenger.name} (ID: {passenger.id}) купил новый билет {ticket_data['ticketId']}")

    except requests.RequestException as e:
        logger.error(f"Ошибка при покупке нового билета для {passenger.name}: {e}")

# Функция обновления статуса пассажиров после закрытия регистрации
def update_passenger_status_after_registration():
    """
    Проверяет, закрыта ли регистрация на рейс, и меняет статус пассажиров с "GotTicket" на "CameToAirport".
    """
    passengers_snapshot = list(passengers_db.values())  # Создаем копию списка пассажиров
    checked_flights = {}  # Кэш для статусов рейсов

    for passenger in passengers_snapshot:
        if passenger.state == "GotTicket":
            if passenger.flightId not in checked_flights:
                flight_data = check_flight(passenger.flightId)
                checked_flights[passenger.flightId] = flight_data["status"]  # Запоминаем статус рейса

            if checked_flights[passenger.flightId] == "RegistrationClosed" or checked_flights[passenger.flightId] == "Departed":
                passenger.state = "CameToAirport"

                # Запускаем покупку нового билета через 10 секунд
                Timer(10, buy_new_ticket, [passenger]).start()

                logger.info(f"Пассажир {passenger.name} (ID: {passenger.id}) теперь в статусе 'CameToAirport', так как регистрация закрыта.")



# Инициализация планировщика
scheduler = BackgroundScheduler()
scheduler.add_job(generate_passenger, 'interval', seconds=3)
scheduler.add_job(print_passengers_table, 'interval', seconds=60)
scheduler.add_job(auto_checkin_passengers, 'interval', seconds=5)
scheduler.add_job(update_passenger_status_after_registration, 'interval', seconds=120)


# Запуск приложения
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск модуля Пассажиры")
    scheduler.start()
    yield
    scheduler.shutdown()
    logger.info("Остановка модуля Пассажиры")


app = FastAPI(title="Passengers Module", lifespan=lifespan)


# Создание пассажира вручную через API
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

# Получение пассажиров по рейсу (возвращаются только те, кто CheckedIn)
@app.get("/v1/passengersId/flight/{flightId}", response_model=PassengersIDs)
def get_passenger_ids_by_flight(flightId: str):
    """
    Получение списка ID пассажиров с рейса, у которых статус CheckedIn.
    """
    checked_in_passengers = [p for p in passengers_db.values() if p.flightId == flightId and p.state == "CheckedIn"]

    if checked_in_passengers:  # Проверяем, есть ли пассажиры со статусом CheckedIn
        for passenger in checked_in_passengers:
            passenger.state = "OnBus"  # Меняем статус на "OnBus"

    logger.info(f"Запрошены ID пассажиров рейса {flightId} со статусом CheckedIn: найдено {len(checked_in_passengers)}")

    return {"passengers": [p.id for p in checked_in_passengers]}


@app.post("/v1/passengers/board", response_model=dict)
def mark_passengers_onboard(passenger_ids: List[str] = Body(...)):
    """
    Устанавливает статус "Boarded" для списка пассажиров, которые были CheckedIn.
    """
    updated_count = 0

    for passenger_id in passenger_ids:
        passenger = passengers_db.get(passenger_id)
        if passenger and (passenger.state == "CheckedIn" or passenger.state == "OnBus"):
            passenger.state = "Boarded"
            updated_count += 1
            logger.info(f"Пассажир {passenger.name} (ID: {passenger_id}) теперь на борту (Boarded).")

    return {"status": "success", "updated": updated_count}



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
    if not passenger or passenger.state != "GotTicket":
        raise HTTPException(status_code=400, detail="Пассажир не может зарегистрироваться")

    # Если существует forgedTicket, используем его номер для регистрации
    if passenger.forgedTicket:
        ticket_id_for_checkin = passenger.forgedTicket.ticketId
        logger.info(f"Пассажир {passenger.name} проходит с подделанным билетом {ticket_id_for_checkin}")
    else:
        ticket_id_for_checkin = passenger.ticket.ticketId

    try:
        checkin_response = requests.post(
            f"{CHECKIN_API_URL}/start",
            json={
                "flightId": passenger.flightId,
                "passengerId": passenger.id,
                "ticketId": ticket_id_for_checkin
            }
        )
        checkin_response.raise_for_status()
        checkin_data = checkin_response.json()
        if passenger.forgedTicket:
            passenger.state = "CameToAirport"
            logger.info(f"Пассажир {passenger.name} вернулся в аэропорт, не прошел регистрацию с подделанным билетом")
        else:
            passenger.state = "CheckedIn"
            logger.info(f"Пассажир {passenger.name} зарегистрирован, checkInId: {checkin_data['checkInId']}")
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


def get_reg_flights() -> List[dict]:
    try:
        response = requests.get(TABLO_API_URL)
        response.raise_for_status()
        flights = response.json()
        # Возвращаем рейсы с регистрацией (RegistrationOpen или RegistrationClosed)
        reg_flights = [f for f in flights if f["status"] in ["RegistrationOpen", "RegistrationClosed"]]
        logger.debug(f"Получено {len(reg_flights)} рейсов для регистрации")
        return reg_flights
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе рейсов для регистрации: {e}")
        return []

def update_passenger_ticket(passenger):
    try:
        response = requests.get(f"http://172.20.10.2:8005/v1/tickets/passenger/{passenger.id}")
        response.raise_for_status()
        tickets = response.json()
        active_tickets = [t for t in tickets if t["status"] == "active"]
        # Если у пассажира уже есть forgedTicket, не меняем его
        if active_tickets and not passenger.forgedTicket:
            passenger.ticket = Ticket(**active_tickets[0])
        elif not active_tickets:
            passenger.ticket = None
    except Exception as e:
        logger.error(f"Ошибка обновления билета для пассажира {passenger.id}: {e}")

# UI: Главная страница
@app.get("/ui", response_class=HTMLResponse)
async def ui_home(request: Request):
    available_flights = get_available_flights()  # Только рейсы со статусом "Scheduled"
    reg_flights = get_reg_flights()  # Рейсы, где регистрация открыта или закрыта
    passengers = list(passengers_db.values())

    # Обновляем информацию о билетах для каждого пассажира
    for p in passengers:
        update_passenger_ticket(p)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "passengers": passengers,
            "faked_tickets": faked_tickets,
            "available_flights": available_flights,
            "reg_flights": reg_flights,
            "flights": available_flights  # Для массового создания используем доступные рейсы
        }
    )


# UI: Создание пассажира
@app.post("/ui/create_passenger", response_class=RedirectResponse)
async def ui_create_passenger(request: Request, name: str = Form(...), flightId: str = Form(...),
                              baggageWeight: int = Form(...), menuType: str = Form(...),
                              isVIP: Optional[bool] = Form(False)):
    try:
        passenger = create_passenger_instance(name, flightId, baggageWeight, menuType, isVIP)
        logger.info(f"Пассажир {name} создан через UI, ID: {passenger.id}")
    except HTTPException as e:
        logger.error(f"Ошибка при создании пассажира через UI: {e.detail}")
        return templates.TemplateResponse("index.html", {"request": request, "passengers": list(passengers_db.values()),
                                                         "error": e.detail})
    return RedirectResponse(url="/ui", status_code=303)

# UI: Массовое создание пассажиров
@app.post("/ui/create_bulk_passengers", response_class=RedirectResponse)
async def ui_create_bulk_passengers(request: Request, bulk_count: int = Form(...), bulk_flightId: str = Form(...)):
    created_ids = []
    for i in range(bulk_count):
        name = random.choice(NAMES)
        baggageWeight = random.randint(0, 20)
        menuType = random.choice(MENU_TYPES)
        isVIP = random.random() < 0.2
        try:
            passenger = create_passenger_instance(name, bulk_flightId, baggageWeight, menuType, isVIP)
            created_ids.append(passenger.id)
        except HTTPException as e:
            logger.error(f"Ошибка при создании пассажира {name}: {e.detail}")
            # Продолжаем создавать остальных даже если один не удался.
            continue
    logger.info(f"Создано пассажиров: {len(created_ids)}")
    return RedirectResponse(url="/ui", status_code=303)

# UI: Массовая регистрация всех пассажиров рейса
@app.post("/ui/register_all", response_class=RedirectResponse)
async def ui_register_all(request: Request, flightId: str = Form(...)):
    flight_data = check_flight(flightId)
    if flight_data["status"] not in ["RegistrationOpen", "RegistrationClosed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Регистрация для рейса {flightId} невозможна, статус: {flight_data['status']}"
        )

    count_registered = 0
    for passenger in passengers_db.values():
        if passenger.flightId == flightId and passenger.state == "GotTicket" and passenger.ticket:
            try:
                if passenger.forgedTicket:
                    ticket_id_for_checkin = passenger.forgedTicket.ticketId
                    logger.info(
                        f"Пассажир {passenger.name} пытается пройти с подделанным билетом {ticket_id_for_checkin}")
                else:
                    ticket_id_for_checkin = passenger.ticket.ticketId

                # Проверяем, является ли ticket словарем, и преобразуем в объект Ticket
                if isinstance(passenger.ticket, dict):
                    passenger.ticket = Ticket(**passenger.ticket)
                checkin_response = requests.post(f"{CHECKIN_API_URL}/start", json={
                    "flightId": passenger.flightId,
                    "passengerId": passenger.id,
                    "ticketId": ticket_id_for_checkin  # Теперь ticketId доступен
                })
                checkin_response.raise_for_status()
                checkin_data = checkin_response.json()
                if passenger.forgedTicket:
                    passenger.state = "CameToAirport"
                    logger.info(
                        f"Пассажир {passenger.name} вернулся в аэропорт, не прошел регистрацию с подделанным билетом")
                else:
                    passenger.state = "CheckedIn"
                    logger.info(f"Пассажир {passenger.name} зарегистрирован, checkInId: {checkin_data['checkInId']}")
                    count_registered += 1
            except requests.RequestException as e:
                logger.error(f"Ошибка регистрации пассажира {passenger.name}: {e}")
                continue

    logger.info(f"Зарегистрировано пассажиров: {count_registered} для рейса {flightId}")
    return RedirectResponse(url="/ui", status_code=303)

# UI: Установка/переключение VIP-статуса
@app.post("/ui/toggle_vip", response_class=RedirectResponse)
async def ui_toggle_vip(passenger_id: str = Form(...)):
    passenger = passengers_db.get(passenger_id)
    if not passenger:
        raise HTTPException(status_code=404, detail="Пассажир не найден")

    # Получаем информацию о рейсе, к которому приписан пассажир
    flight_data = check_flight(passenger.flightId)
    # Разрешаем переключать VIP-статус только при статусе Scheduled
    if flight_data["status"] != "Scheduled":
        raise HTTPException(
            status_code=400,
            detail=f"Нельзя переключить VIP-статус: рейс имеет статус {flight_data['status']}. Требуется Scheduled."
        )

    # Если статус рейса — Scheduled, переключаем статус VIP
    passenger.isVIP = not passenger.isVIP
    logger.info(f"Пассажир {passenger.name} (ID: {passenger.id}) VIP статус изменён на {passenger.isVIP}")
    return RedirectResponse(url="/ui", status_code=303)


# UI: Подделка билета
@app.post("/ui/fake_ticket", response_class=RedirectResponse)
async def ui_fake_ticket(passenger_id: str = Form(...)):
    passenger = passengers_db.get(passenger_id)
    if not passenger or not passenger.ticket:
        raise HTTPException(status_code=400, detail="Пассажир не найден или нет билета")

    flight_data = check_flight(passenger.flightId)
    if flight_data["status"] != "Scheduled":
        raise HTTPException(status_code=400, detail=f"Подделка невозможна, статус рейса: {flight_data['status']}")

    old_ticket_id = passenger.ticket.ticketId
    new_ticket_id = str(uuid.uuid4())
    new_ticket_data = passenger.ticket.dict()
    new_ticket_data["ticketId"] = new_ticket_id
    new_ticket_data["status"] = "fake"
    new_ticket_data["isFake"] = True

    # Сохраняем подделанный билет отдельно; оригинальный билет (passenger.ticket) остается без изменений
    passenger.forgedTicket = Ticket(**new_ticket_data)

    # Можно добавить запись в faked_tickets, если требуется для отображения
    faked_tickets.add(passenger_id)

    logger.info(f"Билет пассажира {passenger.name} подделан: {old_ticket_id} -> {new_ticket_id}")
    return RedirectResponse(url="/ui", status_code=303)



if __name__ == "__main__":
    uvicorn.run("passengers_api:app", host="localhost", port=8004, reload=True)