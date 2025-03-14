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
            flightId="FL999",
            planeId="PL-1",
            type="arrive",  # Прилетает к нам
            fromCity="Berlin",
            toCity="Moscow",
            scheduledTime=datetime(2025, 3, 15, 8, 0),
            arrivalTime=None,
            status="PlanningArrive",
            gate="G-41",
            planeParking="P-4",
            runway="R-1",
            requiredFuel=3000
        ),
        FlightData(
            flightId="FL123",
            planeId="PL-5",
            type="depart",  # Улетает от нас
            fromCity="Moscow",
            toCity="Paris",
            scheduledTime=datetime(2025, 3, 15, 9, 0),
            arrivalTime=None,
            status="Scheduled",
            gate="G-11",
            planeParking="P-1",
            runway="R-1",
            requiredFuel=3000
        ),
        FlightData(
            flightId="FL789",
            planeId="PL-3",
            type="depart",
            fromCity="Moscow",
            toCity="Prague",
            scheduledTime=datetime(2025, 3, 15, 15, 0),
            arrivalTime=None,
            status="Scheduled",
            gate="G-31",
            planeParking="P-3",
            runway="R-1",
            requiredFuel=3000
        ),
        FlightData(
            flightId="FL555",
            planeId="PL-4",
            type="arrive",
            fromCity="Nice",
            toCity="Moscow",
            scheduledTime=datetime(2025, 3, 15, 13, 0),
            arrivalTime=None,
            status="PlanningArrive",
            gate="G-51",
            planeParking="P-5",
            runway="R-1",
            requiredFuel=3000
        ),
        FlightData(
            flightId="FL456",
            planeId="PL-2",
            type="depart",  # Улетает от нас
            fromCity="Moscow",
            toCity="London",
            scheduledTime=datetime(2025, 3, 15, 19, 0),
            arrivalTime=None,
            status="Scheduled",
            gate="G-21",
            planeParking="P-2",
            runway="R-1",
            requiredFuel=3000
        ),
    ]
    return {flight.flightId: flight for flight in sorted(demo_flights, key=lambda f: f.scheduledTime)}


flights_db = load_demo_flights()
