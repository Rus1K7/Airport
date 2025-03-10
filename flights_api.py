import uvicorn
from fastapi import FastAPI, HTTPException, Query, Body
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import logging
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
import pika
import json

from db import flights_db, FlightData
from time_control import get_simulation_time, start_time_simulation

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("FlightsAPI")

# Настройка RabbitMQ
RABBITMQ_URL = "amqp://xnyyznus:OSOOLzaQHT5Ys6NPEMAU5DxTChNu2MUe@hawk.rmq.cloudamqp.com:5672/xnyyznus"
QUEUE_NAME = "flight.status.changed"


def publish_to_rabbitmq(flight_id: str, status: str):
    try:
        parameters = pika.URLParameters(RABBITMQ_URL)
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()

        # Passively check if the queue exists (won't modify it)
        channel.queue_declare(queue=QUEUE_NAME, passive=True)

        # If we reach here, the queue exists and we can proceed
        message = {"flightId": flight_id, "status": status}
        body = json.dumps(message)
        channel.basic_publish(
            exchange='',
            routing_key=QUEUE_NAME,
            body=body.encode(),
            properties=pika.BasicProperties(delivery_mode=2)
        )
        logger.info(f"Отправлено сообщение в RabbitMQ: {message}")
        connection.close()
    except pika.exceptions.ChannelClosedByBroker as e:
        # If passive declaration fails due to mismatch, handle it
        if e.reply_code == 406:
            logger.warning("Queue exists with incompatible settings. Consider aligning settings or deleting the queue.")
        raise
    except Exception as e:
        logger.error(f"Ошибка при отправке в RabbitMQ: {e}")
        raise

scheduler = BackgroundScheduler()


def update_flight_statuses():
    sim_time = get_simulation_time()
    for flight in flights_db.values():
        old_status = flight.status
        if flight.status in ["Cancelled", "Delayed", "Arrived", "Departed"]:
            continue

        delta = flight.scheduledTime - sim_time
        minutes_left = delta.total_seconds() / 60.0

        if flight.type == "depart":
            if minutes_left <= 0:
                flight.status = "Departed"
            elif minutes_left <= 5:
                flight.status = "Boarding"
            elif minutes_left <= 10:
                flight.status = "RegistrationClosed"
            elif minutes_left <= 40:
                flight.status = "RegistrationOpen"
            else:
                flight.status = "Scheduled"
        elif flight.type == "arrive":
            if minutes_left <= 0:
                flight.status = "Arrived"
            else:
                # Для прилета можно оставить статус Scheduled (или задать иной, например "EnRoute")
                flight.status = "Scheduled"

        # Если статус изменился, отправляем сообщение в RabbitMQ
        if old_status != flight.status:
            publish_to_rabbitmq(flight.flightId, flight.status)

    logger.info(f"Статусы рейсов обновлены (Время: {sim_time.strftime('%Y-%m-%d %H:%M:%S')})")


scheduler.add_job(update_flight_statuses, 'interval', seconds=1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_time_simulation()
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Information Panel (Tablo)", lifespan=lifespan)


@app.get("/v1/flights", response_model=List[FlightData])
def get_all_flights(flight_type: Optional[str] = Query(None, pattern="^(arrive|depart)$")):
    all_flights = list(flights_db.values())
    if flight_type:
        return [f for f in all_flights if f.type == flight_type]
    return all_flights


@app.get("/v1/flights/{flightId}", response_model=FlightData)
def get_flight_by_id(flightId: str):
    flight = flights_db.get(flightId)
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    return flight


@app.patch("/v1/flights/{flightId}", response_model=FlightData)
def patch_flight(
        flightId: str,
        status: Optional[str] = Body(None),
        gate: Optional[str] = Body(None),
        planeParking: Optional[str] = Body(None),
        runway: Optional[str] = Body(None)
):
    flight = flights_db.get(flightId)
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")

    old_status = flight.status
    if status:
        flight.status = status
    if gate is not None:
        flight.gate = gate
    if planeParking is not None:
        flight.planeParking = planeParking
    if runway is not None:
        flight.runway = runway

    # Если статус изменился, отправляем сообщение в RabbitMQ
    if status and old_status != flight.status:
        publish_to_rabbitmq(flight.flightId, flight.status)

    return flight


@app.get("/v1/simulation/time")
def get_simulation_time_endpoint():
    sim_time = get_simulation_time()
    return {"simulation_time": sim_time.strftime("%Y-%m-%d %H:%M:%S")}


@app.post("/v1/simulation/time")
def set_simulation_time_endpoint(new_time: datetime = Body(...)):
    from time_control import set_simulation_time
    set_simulation_time(new_time)
    return {"message": f"Simulation time set to {new_time.strftime('%Y-%m-%d %H:%M:%S')}"}


@app.patch("/v1/simulation/speed")
def set_simulation_speed_endpoint(speed: int = Body(...)):
    from time_control import set_simulation_speed
    if speed < 1 or speed > 3600:
        raise HTTPException(status_code=400, detail="Speed must be between 1 and 3600")
    set_simulation_speed(speed)
    return {"message": f"Simulation speed set to {speed}x"}


if __name__ == "__main__":
    uvicorn.run("flights_api:app", host="localhost", port=8003, reload=True)  # Исправил хост на ваш предыдущий