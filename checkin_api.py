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
PASSENGERS_API_URL = "http://localhost:8004/v1/passengers"
TICKETS_API_URL = "http://172.20.10.2:8005/v1/tickets"

# FLIGHTS_API_URL = "http://172.20.10.2:8003/v1/flights"
FLIGHTS_API_URL = "http://localhost:8003/v1/flights"  # Табло

BAGGAGE_API_URL = "http://172.20.10.2:8007/v1/baggage"      # Baggage Warehouse
BAGGAGE_TRACK_API_URL = "http://localhost:8011/v1/baggage-track"  # Замените на актуальный адрес

CATERING_API_URL = "http://small-doors-punch.loca.lt/v1/catering"    # Catering Truck

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
        flight_response = requests.get(f"{FLIGHTS_API_URL}/{flightId}")
        flight_response.raise_for_status()
        flight = flight_response.json()
        logger.info(f"Получены данные о рейсе {flightId}: статус {flight.get('status')}")
        if flight["status"] not in ["RegistrationOpen", "RegistrationClosed"]:
            logger.error("Регистрация на рейс невозможна, статус рейса: " + flight["status"])
            raise HTTPException(status_code=400, detail="Регистрация на рейс невозможна")

        # Попытка получить билет из tickets_for_checkin
        valid_tickets = tickets_for_checkin.get(flightId, [])
        if not valid_tickets:
            logger.warning(f"Список билетов для рейса {flightId} пуст. Возможно, билеты не были переданы в Check-In.")
            # Дополнительно можно попытаться получить билет напрямую или сообщить о необходимости обновить список билетов.
            raise HTTPException(status_code=400, detail="Билет недействителен или подделан")

        ticket = next((t for t in valid_tickets if t["ticketId"] == ticketId), None)
        if not ticket:
            logger.error(f"Билет {ticketId} не найден в списке билетов для рейса {flightId}")
            raise HTTPException(status_code=400, detail="Билет недействителен или подделан")
        if ticket["status"] != "active" or ticket.get("isFake", False):
            logger.error("Билет не соответствует требованиям регистрации")
            raise HTTPException(status_code=400, detail="Билет не соответствует пассажиру или рейсу")

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
    # Логируем полученный запрос полностью
    logger.info(f"Получен запрос на обновление билетов для рейса {flightId}: {request.tickets}")
    tickets_for_checkin[flightId] = request.tickets
    logger.info(f"Обновлен список билетов для рейса {flightId}: {tickets_for_checkin[flightId]}")
    return {"status": "success", "message": f"Получено {len(request.tickets)} билетов для рейса {flightId}"}


# Эндпоинт для начала регистрации пассажира
@app.post("/v1/checkin/start", response_model=dict, status_code=201)
def start_checkin(request: CheckInRequest = Body(...)):
    max_retries = 3
    attempt = 0
    while attempt < max_retries:
        try:
            checkin_id = str(uuid4())
            logger.info(
                f"Получен запрос на регистрацию: рейс {request.flightId}, пассажир {request.passengerId}, билет {request.ticketId}")

            # Попытка проверки билета и рейса
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
            logger.info(f"Пассажир {request.passengerId} успешно зарегистрирован, checkInId: {checkin_id}")

            # Проверяем, завершена ли регистрация на рейс
            if is_registration_complete(request.flightId):
                menu_summary = get_menu_for_flight(request.flightId)["menuSummary"]
                logger.info(f"Все пассажиры рейса {request.flightId} зарегистрированы, отправляем меню: {menu_summary}")

                try:
                    response = requests.post(f"{CATERING_API_URL}/order",
                                             json={"flightId": request.flightId, "menu": menu_summary})
                    response.raise_for_status()
                    logger.info(
                        f"Меню для рейса {request.flightId} успешно отправлено в Catering Truck: {response.json()}")
                except requests.RequestException as e:
                    logger.error(f"Ошибка при отправке меню: {e}")

            logger.info(f"Пассажир {request.passengerId} успешно зарегистрирован, checkInId: {checkin_id}")

            # Автоматическая отправка багажа в Baggage Track
            try:
                baggage_data = {
                    "flightId": request.flightId,
                    "passengerId": request.passengerId,
                    "ticketId": request.ticketId,
                    "baggageWeight": ticket.get("baggageWeight", 0),
                    "baggageItems": ticket.get("baggageItems", [])
                }

                response = requests.post(f"{BAGGAGE_TRACK_API_URL}/register", json=baggage_data)
                response.raise_for_status()
                logger.info(f"Багаж пассажира {request.passengerId} успешно отправлен в Baggage Track.")

            except requests.RequestException as e:
                logger.error(f"Ошибка при отправке багажа в Baggage Track: {e}")

            return {"checkInId": checkin_id, "status": "Completed"}
        except HTTPException as exc:
            # Если ошибка связана с отсутствием билета (код 400), пробуем повторить регистрацию
            if exc.status_code == 400:
                attempt += 1
                logger.error(
                    f"Ошибка регистрации для пассажира {request.passengerId} (попытка {attempt}/{max_retries}): {exc.detail}")
                import time
                time.sleep(2)  # Задержка 2 секунды перед повторной попыткой
                continue
            else:
                # Для других ошибок немедленно выбрасываем исключение
                raise exc
    # Если все попытки не увенчались успехом, возвращаем ошибку
    logger.error(f"Регистрация для пассажира {request.passengerId} не удалась после {max_retries} попыток")
    raise HTTPException(status_code=400, detail="Регистрация не удалась после нескольких попыток")


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

@app.post("/v1/checkin/{checkInId}/baggage-track", response_model=dict)
def send_baggage_to_track(checkInId: str):
    """
    Отправляет данные о багаже пассажира в Baggage Track после регистрации.
    """
    checkin = checkin_db.get(checkInId)
    if not checkin or checkin.taskType != "registration":
        logger.error(f"Ошибка: регистрация с ID {checkInId} не найдена или неверного типа")
        raise HTTPException(status_code=404, detail="Регистрация не найдена или не завершена")

    # Получаем данные о пассажире
    try:
        passenger_response = requests.get(f"{PASSENGERS_API_URL}/{checkin.passengerId}")
        passenger_response.raise_for_status()
        passenger = passenger_response.json()
        logger.info(f"Данные о пассажире {checkin.passengerId} получены")
    except requests.RequestException as e:
        logger.error(f"Ошибка при получении данных пассажира {checkin.passengerId}: {e}")
        raise HTTPException(status_code=503, detail="Ошибка получения данных пассажира")

    # Формируем данные о багаже для `Baggage Track`
    baggage_data = {
        "flightId": checkin.flightId,
        "passengerId": checkin.passengerId,
        "ticketId": checkin.ticketId,
        "baggageWeight": passenger.get("baggageWeight", 0),
        "baggageItems": passenger.get("baggageItems", []) or []  # Исправление: если `None`, передаём []
    }

    # Отправляем данные в Baggage Track
    try:
        response = requests.post(f"{BAGGAGE_TRACK_API_URL}/register", json=baggage_data)
        response.raise_for_status()
        logger.info(f"Багаж пассажира {checkin.passengerId} отправлен в Baggage Track")
    except requests.RequestException as e:
        logger.error(f"Ошибка при отправке багажа в Baggage Track: {e}")
        raise HTTPException(status_code=503, detail="Ошибка при отправке данных о багаже в Baggage Track")

    return {"status": "success", "message": "Baggage sent to Baggage Track", "flightId": checkin.flightId}


@app.get("/v1/checkin/{flightId}/menu", response_model=dict)
def get_menu_for_flight(flightId: str):
    menu_data = {"chicken": 0, "pork": 0, "fish": 0, "vegetarian": 0}
    for checkin in checkin_db.values():
        if checkin.flightId == flightId and checkin.taskType == "registration":
            meal = checkin.details.get("mealPreference", "chicken")
            menu_data[meal] = menu_data.get(meal, 0) + 1
    return {"status": "success", "menuSummary": menu_data}

# Эндпоинт для отправки данных о меню в Catering Truck
"""def is_registration_complete(flight_id: str) -> bool:
    tickets = [t["ticketId"] for t in tickets_for_checkin.get(flight_id, [])]
    registered = [c.ticketId for c in checkin_db.values() if c.flightId == flight_id]
    return set(tickets) == set(registered)"""


def is_registration_complete(flight_id: str) -> bool:
    tickets = {t["ticketId"] for t in tickets_for_checkin.get(flight_id, [])}
    registered = {c.ticketId for c in checkin_db.values() if c.flightId == flight_id}

    if not tickets:
        logger.warning(f"Билеты для рейса {flight_id} ещё не загружены. Регистрация не завершена.")
        return False  # Если нет билетов, считаем, что регистрация не завершена

    return tickets == registered



@app.post("/v1/checkin/{checkInId}/menu", response_model=dict)
def send_menu(checkInId: str):
    checkin = checkin_db.get(checkInId)
    if not checkin or checkin.taskType != "registration":
        raise HTTPException(status_code=404, detail="Регистрация не найдена или не завершена")

    # Проверяем, завершена ли регистрация
    if not is_registration_complete(checkin.flightId):
        logger.warning(f"Попытка отправки меню на Catering Truck ДО завершения регистрации рейса {checkin.flightId}")
        return {"status": "pending", "message": "Регистрация рейса еще не завершена"}

    menu_data = get_menu_for_flight(checkin.flightId)["menuSummary"]

    try:
        response = requests.post(f"{CATERING_API_URL}/order", json={"flightId": checkin.flightId, "menu": menu_data})
        response.raise_for_status()
        logger.info(f"Данные о питании для рейса {checkin.flightId} отправлены в Catering Truck")
    except requests.RequestException as e:
        logger.error(f"Ошибка при отправке меню: {e}")
        raise HTTPException(status_code=503, detail="Ошибка при отправке данных о питании")

    return {
        "status": "success",
        "message": "Menu data sent",
        "flightId": checkin.flightId,
        "menuSummary": menu_data
    }


if __name__ == "__main__":
    uvicorn.run("checkin_api:app", host="172.20.10.2", port=8006, reload=True)