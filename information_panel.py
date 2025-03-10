import uvicorn
from fastapi import FastAPI, HTTPException, Query, Body
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timedelta
import logging
from contextlib import asynccontextmanager

# APScheduler для фонового обновления статусов
from apscheduler.schedulers.background import BackgroundScheduler

app = FastAPI(title="Information Panel (Tablo)")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("InformationPanel")


# ----- Модель рейса -----
class Flight(BaseModel):
    flightId: str
    planeId: Optional[str] = None
    type: str = Field(..., pattern="^(arrive|depart)$", description="arrive или depart")
    fromCity: Optional[str] = None
    toCity: Optional[str] = None
    scheduledTime: datetime
    arrivalTime: Optional[datetime] = None
    status: str = Field("Scheduled", description="Например: Scheduled, RegistrationOpen, Boarding, Departed, Arrived, Cancelled, Delayed")
    gate: Optional[str] = None
    planeParking: Optional[str] = None
    runway: Optional[str] = None

# ----- База рейсов (in-memory) -----
flights_db = {}


# Демонстрационные рейсы. В реальном проекте можно читать из БД.
def load_demo_flights():
    now = datetime.now()
    flights_demo = [
        Flight(
            flightId="FL123",
            planeId="PL001",
            type="depart",
            fromCity="Moscow",
            toCity="Paris",
            scheduledTime=(now + timedelta(minutes=30)),  # вылет через 30 минут
            arrivalTime=None,
            status="Scheduled",
            gate=None,
            planeParking=None,
            runway=None
        ),
        Flight(
            flightId="FL999",
            planeId="PL777",
            type="arrive",
            fromCity="Berlin",
            toCity="Moscow",
            scheduledTime=(now + timedelta(minutes=10)),  # плановое прибытие через 10 минут
            arrivalTime=None,
            status="Scheduled",
            gate="G1",
            planeParking=None,
            runway="R2"
        ),
    ]
    for f in flights_demo:
        flights_db[f.flightId] = f


# ----- Фоновый планировщик для автообновления статусов -----
scheduler = BackgroundScheduler()


def update_flight_statuses():
    """
    Пример упрощенной логики:
    - Если до scheduledTime осталось < 20 мин, статус -> RegistrationOpen
    - Если до scheduledTime осталось < 5 мин, статус -> Boarding
    - Если мы вышли за scheduledTime, статус -> Departed или Arrived
      (Если type=depart -> Departed, если arrive -> Arrived)
    """
    now = datetime.now()
    for flight in flights_db.values():
        if flight.status in ["Cancelled", "Delayed", "Arrived", "Departed"]:
            continue  # не меняем статус для уже финализированных или отмененных

        delta = flight.scheduledTime - now
        minutes_left = delta.total_seconds() / 60.0

        if minutes_left <= 0:
            # Рейс уже должен был вылететь/прилететь
            if flight.type == "depart":
                flight.status = "Departed"
            else:
                flight.status = "Arrived"
        elif minutes_left <= 5:
            flight.status = "Boarding"
        elif minutes_left <= 20:
            flight.status = "RegistrationOpen"
        else:
            flight.status = "Scheduled"

    logger.info("Автообновление статусов рейсов завершено")


# Запуск планировщика раз в 10 секунд (пример)
scheduler.add_job(update_flight_statuses, 'interval', seconds=10)


# ----- Lifespan (замена on_event) -----
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Запуск и остановка фоновых процессов"""
    load_demo_flights()  # Заполняем рейсы демоданными
    scheduler.start()
    yield
    scheduler.shutdown()  # Останавливаем планировщик


app = FastAPI(title="Information Panel (Tablo)", lifespan=lifespan)


# ----- ЭНДПОИНТЫ -----

@app.get("/v1/flights", response_model=List[Flight])
def get_all_flights(flight_type: Optional[str] = Query(None, pattern="^(arrive|depart)$")):
    """
    Получить список всех рейсов.
    Если передан параметр flight_type=arrive/depart, фильтруем по типу.
    """
    all_flights = list(flights_db.values())
    if flight_type:
        return [f for f in all_flights if f.type == flight_type]
    return all_flights


@app.get("/v1/flights/{flightId}", response_model=Flight)
def get_flight_by_id(flightId: str):
    """
    Получить конкретный рейс по flightId.
    """
    flight = flights_db.get(flightId)
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    return flight


@app.patch("/v1/flights/{flightId}", response_model=Flight)
def patch_flight(flightId: str,
                 status: Optional[str] = Body(None),
                 gate: Optional[str] = Body(None),
                 planeParking: Optional[str] = Body(None),
                 runway: Optional[str] = Body(None)):
    """
    Ручное обновление некоторых полей рейса (статус, гейт, парковка, ВПП).
    """
    flight = flights_db.get(flightId)
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    if status:
        flight.status = status
    if gate is not None:
        flight.gate = gate
    if planeParking is not None:
        flight.planeParking = planeParking
    if runway is not None:
        flight.runway = runway

    return flight


# ----- Запуск сервера -----
if __name__ == "__main__":
    uvicorn.run("information_panel:app", host="0.0.0.0", port=8003, reload=True)
