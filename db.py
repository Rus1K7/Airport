from datetime import datetime
from pydantic import BaseModel
from typing import Optional

class FlightData(BaseModel):
    flightId: str
    planeId: Optional[str] = None
    type: str  # "arrive" или "depart"
    fromCity: Optional[str] = None
    toCity: Optional[str] = None
    scheduledTime: datetime
    arrivalTime: Optional[datetime] = None
    status: str = "Scheduled"
    gate: Optional[str] = None
    planeParking: Optional[str] = None
    runway: Optional[str] = None
    requiredFuel: int

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()  # Для корректной сериализации datetime в JSON
        }

def load_demo_flights():
    demo_flights = [
        FlightData(
            flightId="FL123",
            planeId="PL001",
            type="depart",  # Улетает от нас
            fromCity="Moscow",
            toCity="Paris",
            scheduledTime=datetime(2025, 3, 15, 9, 0),
            arrivalTime=None,
            status="Scheduled",
            gate="G-1",  # Назначаем гейт, так как "depart"
            planeParking="P-5",  # Назначаем парковку, так как "depart"
            runway=None,
            requiredFuel=3000
        ),
        FlightData(
            flightId="FL999",
            planeId="PL777",
            type="arrive",  # Прилетает к нам
            fromCity="Berlin",
            toCity="Moscow",
            scheduledTime=datetime(2025, 3, 15, 9, 30),
            arrivalTime=None,
            status="PlanningArrive",
            gate="G-4",  # Не назначаем гейт, ждём Ground Control
            planeParking="P-4",  # Не назначаем парковку, ждём Ground Control
            runway="R-5",  # Указываем ВПП для посадки
            requiredFuel = 3000
    ),
        FlightData(
            flightId="FL456",
            planeId="PL002",
            type="depart",  # Улетает от нас
            fromCity="Moscow",
            toCity="London",
            scheduledTime=datetime(2025, 3, 15, 19, 0),
            arrivalTime=None,
            status="Scheduled",
            gate="G-3",  # Назначаем гейт, так как "depart"
            planeParking="P-4",  # Назначаем парковку, так как "depart"
            runway=None,
            requiredFuel=3000
        ),
    ]
    return {flight.flightId: flight for flight in demo_flights}

flights_db = load_demo_flights()