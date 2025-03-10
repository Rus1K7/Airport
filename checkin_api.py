import uvicorn
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime
import logging
from contextlib import asynccontextmanager
import requests
from uuid import uuid4

# Настройка логгера
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("CheckInAPI")

# URL модулей (укажите корректные адреса)
PASSENGERS_API_URL = "http://172.20.10.2:8004/v1/passengers"
TICKETS_API_URL = "http://172.20.10.2:8005/v1/tickets"
FLIGHTS_API_URL = "http://172.20.10.2:8003/v1/flights"
BAGGAGE_API_URL = "http://172.20.10.2:8007/v1/baggage"      # Baggage Warehouse
CATERING_API_URL = "http://172.20.10.2:8008/v1/catering"    # Catering Truck

# Модель данных для Check-In задачи
class CheckInData(BaseModel):
    checkInId: str
    taskType: str  # "registration", "baggageDrop", "issueTicket"
    state: str     # "sent", "assigned", "inProgress", "completed"
    flightId: str
    passengerId: str
    ticketId: str
    counter: str
    details: dict

    class Config:
        json_encoders = {"datetime": lambda v: v.isoformat()}

# Модель для начала регистрации
class CheckInRequest(BaseModel):
    flightId: str
    passengerId: str
    ticketId: str

# Модель для получения билетов от Ticket Sales
class TicketsRequest(BaseModel):
    flightId: str
    tickets: List[dict]

# In-memory базы данных
checkin_db: Dict[str, CheckInData] = {}
tickets_for_checkin: Dict[str, List[dict]] = {}  # {flightId: [ticket, ticket, ...]}

# Функция проверки билета и рейса
def validate_ticket_and_flight(flightId: str, passengerId: str, ticketId: str) -> dict:
    logger.info(f"Начало проверки рейса {flightId} для пассажира {passengerId} с билетом {ticketId}")
    try:
        # Проверка рейса через Information Panel
        flight_response = requests.get(f"{FLIGHTS_API_URL}/{flightId}")
        flight_response.raise_for_status()
        flight = flight_response.json()
        logger.info(f"Получены данные о рейсе {flightId}: статус {flight.get('status')}")
        if flight["status"] not in ["RegistrationOpen", "RegistrationClosed"]:
            logger.error("Регистрация на рейс невозможна, статус рейса: " + flight["status"])
            raise HTTPException(status_code=400, detail="Регистрация на рейс невозможна")
        # Проверка билета среди полученных для данного рейса
        valid_tickets = tickets_for_checkin.get(flightId, [])
        logger.info(f"Список билетов для рейса {flightId}: {valid_tickets}")
        ticket = next((t for t in valid_tickets if t["ticketId"] == ticketId), None)
        if not ticket:
            logger.error(f"Билет {ticketId} не найден в списке билетов для рейса {flightId}")
            raise HTTPException(status_code=400, detail="Билет недействителен или подделан")
        if ticket["passengerId"] != passengerId or ticket["flightId"] != flightId or ticket["status"] != "active":
            logger.error("Билет не соответствует пассажиру или рейсу")
            raise HTTPException(status_code=400, detail="Билет не соответствует пассажиру или рейсу")
        return {"ticket": ticket, "flight": flight}
    except requests.RequestException as e:
        logger.error(f"Ошибка при проверке: {e}")
        raise HTTPException(status_code=503, detail="Ошибка проверки билета или рейса")

# Создаем приложение Check-In
app = FastAPI(title="Check-In Module")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск модуля Check-In")
    yield
    logger.info("Остановка модуля Check-In")

app = FastAPI(title="Check-In Module", lifespan=lifespan)

# Эндпоинт для получения билетов от Ticket Sales
@app.post("/v1/checkin/tickets", response_model=dict)
def receive_tickets(request: TicketsRequest = Body(...)):
    flightId = request.flightId
    tickets_for_checkin[flightId] = request.tickets
    logger.info(f"Получено {len(request.tickets)} билетов для рейса {flightId} от Ticket Sales")
    return {"status": "success", "message": f"Получено {len(request.tickets)} билетов для рейса {flightId}"}

# Эндпоинт для начала регистрации пассажира
@app.post("/v1/checkin/start", response_model=dict, status_code=201)
def start_checkin(request: CheckInRequest = Body(...)):
    checkin_id = str(uuid4())
    logger.info(f"Получен запрос на регистрацию: рейс {request.flightId}, пассажир {request.passengerId}, билет {request.ticketId}")
    # Проверка билета и рейса
    data = validate_ticket_and_flight(request.flightId, request.passengerId, request.ticketId)
    ticket = data["ticket"]
    # Создаем запись Check-In
    checkin = CheckInData(
        checkInId=checkin_id,
        taskType="registration",
        state="completed",  # Здесь сразу считаем регистрацию завершённой (можно доработать логику)
        flightId=request.flightId,
        passengerId=request.passengerId,
        ticketId=request.ticketId,
        counter="C1",  # Фиксируем номер стойки регистрации
        details={
            "seatNumber": "12A",
            "mealPreference": ticket["menuType"],
            "frequentFlyer": ticket["isVIP"]
        }
    )
    checkin_db[checkin_id] = checkin
    logger.info(f"Пассажир {request.passengerId} зарегистрирован, checkInId: {checkin_id}")
    return {"checkInId": checkin_id, "status": "Completed"}

# Эндпоинт для получения статуса регистрации
@app.get("/v1/checkin/{checkInId}", response_model=CheckInData)
def get_checkin_status(checkInId: str):
    checkin = checkin_db.get(checkInId)
    if not checkin:
        logger.error(f"Регистрация с ID {checkInId} не найдена")
        raise HTTPException(status_code=404, detail="Регистрация не найдена")
    logger.info(f"Получен статус регистрации для {checkInId}: {checkin.state}")
    return checkin

# Эндпоинт для ручного обновления статуса регистрации
@app.patch("/v1/checkin/{checkInId}", response_model=CheckInData)
def update_checkin(checkInId: str, status: str = Body(..., embed=True)):
    checkin = checkin_db.get(checkInId)
    if not checkin:
        logger.error(f"Регистрация с ID {checkInId} не найдена для обновления")
        raise HTTPException(status_code=404, detail="Регистрация не найдена")
    valid_states = ["sent", "assigned", "inProgress", "completed", "Confirmed", "Rejected"]
    if status not in valid_states:
        logger.error(f"Попытка обновить регистрацию {checkInId} с неверным статусом: {status}")
        raise HTTPException(status_code=400, detail="Неверный статус")
    checkin.state = status
    logger.info(f"Статус регистрации {checkInId} обновлён на {status}")
    return checkin

# Эндпоинт для отправки багажа в Baggage Warehouse
@app.post("/v1/checkin/{checkInId}/baggage", response_model=dict)
def send_baggage(checkInId: str):
    checkin = checkin_db.get(checkInId)
    if not checkin or checkin.taskType != "registration":
        logger.error(f"Невозможно отправить багаж: регистрация {checkInId} не найдена или неверного типа")
        raise HTTPException(status_code=404, detail="Регистрация не найдена или не завершена")
    try:
        passenger_response = requests.get(f"{PASSENGERS_API_URL}/{checkin.passengerId}")
        passenger_response.raise_for_status()
        passenger = passenger_response.json()
        logger.info(f"Получены данные о пассажире {checkin.passengerId} для багажа")
    except requests.RequestException as e:
        logger.error(f"Ошибка получения данных пассажира: {e}")
        raise HTTPException(status_code=503, detail="Ошибка при получении данных пассажира")
    baggage_data = {
        "flightId": checkin.flightId,
        "baggageList": {
            f"baggage_{checkin.passengerId}": {
                "owner": checkin.passengerId,
                "weight": passenger.get("baggageWeight", 0)
            }
        }
    }
    try:
        response = requests.post(f"{BAGGAGE_API_URL}/store", json=baggage_data)
        response.raise_for_status()
        logger.info(f"Багаж для пассажира {checkin.passengerId} отправлен в Baggage Warehouse")
    except requests.RequestException as e:
        logger.error(f"Ошибка при отправке багажа: {e}")
        raise HTTPException(status_code=503, detail="Ошибка при отправке багажа")
    return {"status": "success", "message": "Baggage sent to warehouse", "flightId": checkin.flightId}

# Эндпоинт для отправки данных о меню в Catering Truck
@app.post("/v1/checkin/{checkInId}/menu", response_model=dict)
def send_menu(checkInId: str):
    checkin = checkin_db.get(checkInId)
    if not checkin or checkin.taskType != "registration":
        logger.error(f"Невозможно отправить данные о меню: регистрация {checkInId} не найдена или неверного типа")
        raise HTTPException(status_code=404, detail="Регистрация не найдена или не завершена")
    menu_data = {
        "flightId": checkin.flightId,
        "menu": {"chicken": 0, "pork": 0, "fish": 0, "vegetarian": 0}
    }
    for c in checkin_db.values():
        if c.flightId == checkin.flightId and c.taskType == "registration":
            meal = c.details.get("mealPreference", "chicken")
            menu_data["menu"][meal] = menu_data["menu"].get(meal, 0) + 1
    try:
        response = requests.post(f"{CATERING_API_URL}/order", json=menu_data)
        response.raise_for_status()
        logger.info(f"Данные о питании для рейса {checkin.flightId} отправлены в Catering Truck")
    except requests.RequestException as e:
        logger.error(f"Ошибка при отправке меню: {e}")
        raise HTTPException(status_code=503, detail="Ошибка при отправке данных о питании")
    return {
        "status": "success",
        "message": "Menu data sent",
        "flightId": checkin.flightId,
        "menuSummary": menu_data["menu"]
    }

if __name__ == "__main__":
    uvicorn.run("checkin_api:app", host="172.20.10.2", port=8006, reload=True)
