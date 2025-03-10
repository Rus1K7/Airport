import uvicorn
from fastapi import FastAPI, HTTPException, Query, Body
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import logging
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler

from db import flights_db, FlightData
from time_control import get_simulation_time, start_time_simulation

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("FlightsAPI")

scheduler = BackgroundScheduler()

def update_flight_statuses():
    sim_time = get_simulation_time()
    for flight in flights_db.values():
        if flight.status in ["Cancelled", "Delayed", "Arrived", "Departed"]:
            continue

        delta = flight.scheduledTime - sim_time
        minutes_left = delta.total_seconds() / 60.0

        if minutes_left <= 0:
            flight.status = "Departed" if flight.type == "depart" else "Arrived"
        elif minutes_left <= 5:
            flight.status = "Boarding"
        elif minutes_left <= 40:
            flight.status = "RegistrationOpen"
        else:
            flight.status = "Scheduled"
    logger.info(f"Статусы рейсов обновлены (Время: {sim_time.strftime('%Y-%m-%d %H:%M:%S')})")

# Уменьшаем интервал до 1 секунды для точности (20 минут игрового времени)
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
def patch_flight(flightId: str,
                 status: Optional[str] = Body(None),
                 gate: Optional[str] = Body(None),
                 planeParking: Optional[str] = Body(None),
                 runway: Optional[str] = Body(None)):
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
    uvicorn.run("flights_api:app", host="0.0.0.0", port=8003, reload=True)