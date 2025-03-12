import uvicorn
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, List
import uuid
import requests
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler

# Настройка логгера
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TicketsAPI")

# URL модулей
TABLO_API_URL = "http://localhost:8003/v1/flights"  # Табло

# TABLO_API_URL = "http://172.20.10.2:8003/v1/flights"  # Табло
CHECKIN_API_URL = "http://172.20.10.2:8006/v1/checkin"  # Check-In
MAX_TICKETS_PER_FLIGHT = 100

# Модель для запроса покупки билета
class BuyTicketRequest(BaseModel):
    passengerId: str
    passengerName: str
    flightId: str
    isVIP: bool
    menuType: str
    baggageWeight: int

# Модель для билета
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


# In-memory база билетов и счётчик билетов на рейс
tickets_db = {}
flight_ticket_count = {}
sent_to_checkin = set()  # Множество рейсов, для которых билеты уже отправлены

# Проверка доступности рейса
def check_flight_availability(flightId: str) -> dict:
    try:
        response = requests.get(f"{TABLO_API_URL}/{flightId}")
        response.raise_for_status()
        flight_data = response.json()
        if flight_data["status"] in ["Departed", "Arrived", "Cancelled", "Boarding", "RegistrationClosed", "RegistrationOpen"]:
            logger.error(f"Рейс {flightId} недоступен для покупки билетов (статус: {flight_data['status']})")
            raise HTTPException(status_code=409, detail="Рейс недоступен для покупки билетов")
        current_count = flight_ticket_count.get(flightId, 0)
        if current_count >= MAX_TICKETS_PER_FLIGHT:
            logger.error(f"Превышен лимит билетов для рейса {flightId} ({MAX_TICKETS_PER_FLIGHT})")
            raise HTTPException(status_code=409, detail="Нет свободных мест на рейсе")
        return flight_data
    except requests.RequestException as e:
        logger.error(f"Ошибка при запросе к Табло для рейса {flightId}: {e}")
        raise HTTPException(status_code=404, detail="Рейс не найден или Табло недоступно")

# Функция для автоматической отправки билетов
def auto_send_tickets_to_checkin():
    logger.info("Проверка статусов рейсов для автоматической отправки билетов в Check-In")
    for flightId in flight_ticket_count.keys():
        if flightId in sent_to_checkin:
            continue  # Пропускаем, если билеты уже отправлены

        try:
            response = requests.get(f"{TABLO_API_URL}/{flightId}")
            response.raise_for_status()
            flight_data = response.json()
            current_status = flight_data["status"]
            logger.info(f"Статус рейса {flightId}: {current_status}")

            if current_status == "RegistrationOpen":
                # Фильтруем билеты: выбираем только активные билеты, которые не подделаны
                active_tickets = [
                    ticket.dict() for ticket in tickets_db.values()
                    if ticket.flightId == flightId and ticket.status == "active" and not ticket.isFake
                ]

                if active_tickets:
                    try:
                        checkin_response = requests.post(
                            f"{CHECKIN_API_URL}/tickets",
                            json={"flightId": flightId, "tickets": active_tickets}
                        )
                        checkin_response.raise_for_status()
                        logger.info(f"Отправлено {len(active_tickets)} билетов для рейса {flightId} в Check-In")
                        sent_to_checkin.add(flightId)
                    except requests.RequestException as e:
                        logger.error(f"Ошибка при отправке билетов для рейса {flightId}: {e}")
        except requests.RequestException as e:
            logger.error(f"Ошибка при проверке статуса рейса {flightId}: {e}")


# Инициализация планировщика
scheduler = BackgroundScheduler()
scheduler.add_job(auto_send_tickets_to_checkin, 'interval', seconds=5)  # Проверка каждые 5 секунд

# Жизненный цикл приложения
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск модуля Касса")
    scheduler.start()
    yield
    scheduler.shutdown()
    logger.info("Остановка модуля Касса")

app = FastAPI(title="Ticket Sales Module", lifespan=lifespan)


@app.get("/v1/tickets", response_model=List[Ticket])
def get_all_tickets():
    # Возвращаем только "настоящие" билеты, исключая подделанные
    tickets = [ticket for ticket in tickets_db.values() if ticket.status == "active"]
    logger.info(f"Запрошен список билетов (без подделок): {len(tickets)} записей")
    return tickets


@app.get("/v1/tickets/{ticketId}", response_model=Ticket)
def get_ticket(ticketId: str):
    ticket = tickets_db.get(ticketId)
    if not ticket:
        logger.error(f"Билет с ID {ticketId} не найден")
        raise HTTPException(status_code=404, detail="Билет не найден")
    logger.info(f"Запрошена информация о билете {ticketId}")
    return ticket

@app.get("/v1/tickets/passenger/{passengerId}", response_model=List[Ticket])
def get_tickets_by_passenger(passengerId: str):
    tickets = [ticket for ticket in tickets_db.values() if ticket.passengerId == passengerId]
    logger.info(f"Запрошены билеты пассажира {passengerId}: найдено {len(tickets)}")
    return tickets

@app.post("/v1/tickets/buy", response_model=Ticket, status_code=200)
def buy_ticket(request: BuyTicketRequest = Body(...)):
    flight_data = check_flight_availability(request.flightId)
    ticket_id = str(uuid.uuid4())
    ticket = Ticket(
        ticketId=ticket_id,
        flightId=request.flightId,
        passengerId=request.passengerId,
        passengerName=request.passengerName,
        isVIP=request.isVIP,
        menuType=request.menuType,
        baggageWeight=request.baggageWeight,
        status="active",
        createdAt=datetime.utcnow().isoformat(),
        flightDepartureTime=flight_data["scheduledTime"],
        fromCity=flight_data["fromCity"],
        toCity=flight_data["toCity"]
    )
    tickets_db[ticket_id] = ticket
    flight_ticket_count[request.flightId] = flight_ticket_count.get(request.flightId, 0) + 1
    logger.info(f"Билет {ticket_id} куплен для пассажира {request.passengerName} на рейс {request.flightId}")
    print(f"\n--- Новый билет ---")
    print(f"ID билета: {ticket_id}")
    print(f"Пассажир: {request.passengerName} (ID: {request.passengerId})")
    print(f"Рейс: {request.flightId}")
    print(f"Время вылета: {flight_data['scheduledTime']}")
    print(f"Откуда: {flight_data['fromCity']}")
    print(f"Куда: {flight_data['toCity']}")
    print(f"Тип питания: {request.menuType}")
    print(f"Вес багажа: {request.baggageWeight}")
    print(f"VIP: {request.isVIP}")
    print("-------------------\n")
    return ticket


@app.post("/v1/tickets/refund", response_model=Ticket)
def refund_ticket(ticketId: str, passengerId: str):
    ticket = tickets_db.get(ticketId)
    if not ticket:
        logger.error(f"Билет с ID {ticketId} не найден")
        raise HTTPException(status_code=404, detail="Билет не найден")

    if ticket.passengerId != passengerId:
        logger.error(f"Пассажир {passengerId} не владеет билетом {ticketId}")
        raise HTTPException(status_code=400, detail="Этот билет принадлежит другому пассажиру")

    if ticket.status == "returned":
        logger.error(f"Билет {ticketId} уже возвращён")
        raise HTTPException(status_code=409, detail="Билет уже возвращён")

    # Проверяем статус рейса через Табло
    try:
        response = requests.get(f"{TABLO_API_URL}/{ticket.flightId}")
        response.raise_for_status()
        flight_data = response.json()
        if flight_data["status"] != "Scheduled":
            logger.error(
                f"Рейс {ticket.flightId} имеет статус {flight_data['status']}. Возврат разрешён только для рейсов со статусом Scheduled")
            raise HTTPException(status_code=400, detail="Возврат возможен только для рейсов со статусом Scheduled")
    except requests.RequestException as e:
        logger.error(f"Ошибка проверки рейса {ticket.flightId}: {e}")
        raise HTTPException(status_code=503, detail="Ошибка проверки статуса рейса")

    # Обновляем статус билета и уменьшаем счётчик
    ticket.status = "returned"
    flight_ticket_count[ticket.flightId] = flight_ticket_count.get(ticket.flightId, 0) - 1

    logger.info(f"Билет {ticketId} возвращён для пассажира {ticket.passengerName}")
    print(f"\n--- Возврат билета ---")
    print(f"ID билета: {ticketId}")
    print(f"Пассажир: {ticket.passengerName} (ID: {passengerId})")
    print(f"Рейс: {ticket.flightId}")
    print(f"Статус: {ticket.status}")
    print("---------------------\n")

    return ticket


@app.post("/v1/tickets/send-to-checkin/{flightId}", response_model=dict)
def send_tickets_to_checkin(flightId: str):
    logger.info(f"Попытка отправить билеты для рейса {flightId} в Check-In")
    try:
        response = requests.get(f"{TABLO_API_URL}/{flightId}")
        response.raise_for_status()
        flight_data = response.json()
        if flight_data["status"] != "RegistrationOpen":
            logger.error(f"Регистрация на рейс {flightId} ещё не открыта (статус: {flight_data['status']})")
            raise HTTPException(status_code=400, detail="Регистрация на рейс ещё не открыта")
    except requests.RequestException as e:
        logger.error(f"Ошибка при проверке рейса {flightId}: {e}")
        raise HTTPException(status_code=503, detail="Ошибка при проверке рейса")

    active_tickets = [
        ticket.dict() for ticket in tickets_db.values()
        if ticket.flightId == flightId and ticket.status == "active" and not ticket.isFake
    ]
    logger.info(f"Подготовлено {len(active_tickets)} активных билетов для рейса {flightId}: {[t['ticketId'] for t in active_tickets]}")
    if not active_tickets:
        logger.info(f"Нет активных билетов для рейса {flightId}")
        return {"status": "success", "message": "Нет активных билетов для отправки"}

    try:
        checkin_response = requests.post(
            f"{CHECKIN_API_URL}/tickets",
            json={"flightId": flightId, "tickets": active_tickets}
        )
        checkin_response.raise_for_status()
        logger.info(f"Билеты для рейса {flightId} успешно отправлены в Check-In")
        sent_to_checkin.add(flightId)
        return {"status": "success", "message": f"Отправлено {len(active_tickets)} билетов для рейса {flightId}"}
    except requests.RequestException as e:
        logger.error(f"Ошибка при отправке билетов в Check-In для рейса {flightId}: {e}")
        raise HTTPException(status_code=503, detail="Ошибка при отправке билетов в Check-In")


from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi import Request

templates = Jinja2Templates(directory="templates")

@app.get("/ui/tickets", response_class=HTMLResponse)
async def ui_tickets(request: Request):
    return templates.TemplateResponse("tickets.html", {"request": request})


if __name__ == "__main__":
    uvicorn.run("tickets_api:app", host="172.20.10.2", port=8005, reload=True)