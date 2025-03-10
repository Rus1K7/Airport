import uvicorn
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime
import logging
from contextlib import asynccontextmanager
import pika
import json
import requests
from uuid import uuid4

# Настройка логгера
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("CheckInAPI")

# URL других модулей
PASSENGERS_API_URL = "http://172.20.10.2:8004/v1/passengers"  # Passenger Generator
TICKETS_API_URL = "http://172.20.10.2:8005/v1/tickets"  # Ticket Sales
FLIGHTS_API_URL = "http://172.20.10.2:8003/v1/flights"  # Information Panel

# RabbitMQ
RABBITMQ_URL = "amqp://xnyyznus:OSOOLzaQHT5Ys6NPEMAU5DxTChNu2MUe@hawk.rmq.cloudamqp.com:5672/xnyyznus"
QUEUES = {
    "registration": "tasks.checkin.registration",
    "baggageDrop": "tasks.checkin.baggageDrop",
    "issueTicket": "tasks.checkin.issueTicket"
}


# Модель данных для Check-In
class CheckInData(BaseModel):
    checkInId: str
    taskType: str  # "registration", "baggageDrop", "issueTicket"
    state: str  # "sent", "assigned", "inProgress", "completed"
    flightId: str
    passengerId: str
    ticketId: str
    counter: Optional[str] = None
    details: dict

    class Config:
        json_encoders = {"datetime": lambda v: v.isoformat()}


# Модель для начала регистрации
class CheckInRequest(BaseModel):
    flightId: str
    passengerId: str
    ticketId: str


# База данных Check-In (in-memory)
checkin_db: Dict[str, CheckInData] = {}


# Отправка в RabbitMQ
def publish_to_rabbitmq(queue: str, message: dict):
    try:
        parameters = pika.URLParameters(RABBITMQ_URL)
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        channel.queue_declare(queue=queue, durable=True)
        body = json.dumps(message)
        channel.basic_publish(
            exchange='',
            routing_key=queue,
            body=body.encode(),
            properties=pika.BasicProperties(delivery_mode=2)
        )
        logger.info(f"Отправлено сообщение в {queue}: {message}")
        connection.close()
    except Exception as e:
        logger.error(f"Ошибка при отправке в RabbitMQ: {e}")


# Проверка билета и рейса
def validate_ticket_and_flight(flightId: str, passengerId: str, ticketId: str) -> dict:
    try:
        # Проверка билета
        ticket_response = requests.get(f"{TICKETS_API_URL}/{ticketId}")
        ticket_response.raise_for_status()
        ticket = ticket_response.json()
        if ticket["passengerId"] != passengerId or ticket["flightId"] != flightId or ticket["status"] != "active":
            raise HTTPException(status_code=400, detail="Неверный билет или пассажир")

        # Проверка рейса
        flight_response = requests.get(f"{FLIGHTS_API_URL}/{flightId}")
        flight_response.raise_for_status()
        flight = flight_response.json()
        if flight["status"] not in ["RegistrationOpen", "RegistrationClosed"]:
            raise HTTPException(status_code=400, detail="Регистрация на рейс невозможна")

        return {"ticket": ticket, "flight": flight}
    except requests.RequestException as e:
        logger.error(f"Ошибка при проверке: {e}")
        raise HTTPException(status_code=503, detail="Ошибка проверки билета или рейса")


# Создание приложения
app = FastAPI(title="Check-In Module")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск модуля Check-In")
    yield
    logger.info("Остановка модуля Check-In")


# Эндпоинт для начала регистрации
@app.post("/v1/checkin/start", response_model=dict, status_code=201)
def start_checkin(request: CheckInRequest = Body(...)):
    checkin_id = str(uuid4())

    # Проверка билета и рейса
    data = validate_ticket_and_flight(request.flightId, request.passengerId, request.ticketId)
    ticket = data["ticket"]

    # Создание задачи регистрации
    checkin = CheckInData(
        checkInId=checkin_id,
        taskType="registration",
        state="sent",
        flightId=request.flightId,
        passengerId=request.passengerId,
        ticketId=request.ticketId,
        counter="C1",  # Пример стойки
        details={
            "seatNumber": "12A",  # Пока статично, можно генерировать
            "mealPreference": ticket["menuType"],
            "frequentFlyer": ticket["isVIP"]
        }
    )
    checkin_db[checkin_id] = checkin

    # Отправка в очередь
    message = checkin.dict()
    publish_to_rabbitmq(QUEUES["registration"], message)

    return {"checkInId": checkin_id, "status": "Pending"}


# Получение статуса регистрации
@app.get("/v1/checkin/{checkInId}", response_model=CheckInData)
def get_checkin_status(checkInId: str):
    checkin = checkin_db.get(checkInId)
    if not checkin:
        raise HTTPException(status_code=404, detail="Регистрация не найдена")
    return checkin


# Ручное обновление статуса
@app.patch("/v1/checkin/{checkInId}", response_model=CheckInData)
def update_checkin(checkInId: str, status: str = Body(..., embed=True)):
    checkin = checkin_db.get(checkInId)
    if not checkin:
        raise HTTPException(status_code=404, detail="Регистрация не найдена")
    if status not in ["sent", "assigned", "inProgress", "completed"]:
        raise HTTPException(status_code=400, detail="Неверный статус")

    old_status = checkin.state
    checkin.state = status
    if old_status != status:
        publish_to_rabbitmq(QUEUES[checkin.taskType], checkin.dict())
    return checkin


# Отправка багажа
@app.post("/v1/checkin/{checkInId}/baggage", response_model=dict)
def send_baggage(checkInId: str):
    checkin = checkin_db.get(checkInId)
    if not checkin or checkin.taskType != "registration":
        raise HTTPException(status_code=404, detail="Регистрация не найдена или не завершена")

    # Получение данных о пассажире
    passenger_response = requests.get(f"{PASSENGERS_API_URL}/{checkin.passengerId}")
    passenger_response.raise_for_status()
    passenger = passenger_response.json()

    baggage_task = CheckInData(
        checkInId=str(uuid4()),
        taskType="baggageDrop",
        state="sent",
        flightId=checkin.flightId,
        passengerId=checkin.passengerId,
        ticketId=checkin.ticketId,
        counter=checkin.counter,
        details={
            "luggageCount": 1,  # Предполагаем 1 сумку
            "totalWeight": passenger["baggageWeight"],
            "fragileItems": False
        }
    )
    checkin_db[baggage_task.checkInId] = baggage_task

    # Формирование данных для Baggage Warehouse
    baggage_data = {
        "flightId": checkin.flightId,
        "baggageList": {
            f"baggage_{checkin.passengerId}": {
                "owner": checkin.passengerId,
                "weight": passenger["baggageWeight"]
            }
        }
    }
    publish_to_rabbitmq(QUEUES["baggageDrop"], baggage_task.dict())

    return {"status": "success", "message": "Baggage sent to warehouse", "flightId": checkin.flightId}


# Отправка данных о питании
@app.post("/v1/checkin/{checkInId}/menu", response_model=dict)
def send_menu(checkInId: str):
    checkin = checkin_db.get(checkInId)
    if not checkin or checkin.taskType != "registration":
        raise HTTPException(status_code=404, detail="Регистрация не найдена или не завершена")

    # Подсчет питания (пример, агрегация по рейсу нужна из всех checkins)
    menu_data = {
        "flightId": checkin.flightId,
        "menu": {"chicken": 0, "pork": 0, "fish": 0, "vegetarian": 0}
    }
    for c in checkin_db.values():
        if c.flightId == checkin.flightId and c.taskType == "registration":
            meal = c.details.get("mealPreference", "chicken")
            menu_data["menu"][meal] = menu_data["menu"].get(meal, 0) + 1

    publish_to_rabbitmq(QUEUES["issueTicket"], menu_data)
    return {"status": "success", "message": "Menu data sent", "flightId": checkin.flightId,
            "menuSummary": menu_data["menu"]}


# Запуск
if __name__ == "__main__":
    uvicorn.run("checkin_api:app", host="172.20.10.2", port=8006, reload=True)