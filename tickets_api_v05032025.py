import uvicorn
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, List
import uuid
import requests
import logging
from datetime import datetime
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TicketsAPI")

TABLO_API_URL = "http://localhost:8003/v1/flights"
MAX_TICKETS_PER_FLIGHT = 100

# Модель для тела запроса
class BuyTicketRequest(BaseModel):
    passengerId: str
    passengerName: str
    flightId: str
    isVIP: bool
    menuType: str
    baggageWeight: int

class Ticket(BaseModel):
    ticketId: str
    flightId: str
    passengerId: str
    passengerName: str
    isVIP: bool
    menuType: str
    baggageWeight: int
    status: str = "active"
    createdAt: str = None  # Делаем необязательным с None по умолчанию
    gate: Optional[str] = None
    seatNumber: Optional[str] = None
    flightDepartureTime: Optional[str] = None
    fromCity: Optional[str] = None
    toCity: Optional[str] = None

    class Config:
        json_encoders = {"datetime": lambda v: v.isoformat()}

tickets_db = {}
flight_ticket_count = {}

def check_flight_availability(flightId: str) -> dict:
    try:
        response = requests.get(f"{TABLO_API_URL}/{flightId}")
        response.raise_for_status()
        flight_data = response.json()
        if flight_data["status"] in ["Departed", "Arrived", "Cancelled"]:
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск модуля Касса")
    yield
    logger.info("Остановка модуля Касса")

app = FastAPI(title="Ticket Sales Module", lifespan=lifespan)

@app.get("/v1/tickets", response_model=List[Ticket])
def get_all_tickets():
    tickets = list(tickets_db.values())
    logger.info(f"Запрошен список всех билетов: {len(tickets)} записей")
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
        createdAt=datetime.utcnow().isoformat(),  # Заполняем на сервере
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

if __name__ == "__main__":
    uvicorn.run("tickets_api_v05032025:app", host="0.0.0.0", port=8005, reload=True)