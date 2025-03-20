from pydantic import BaseModel
from typing import Dict, List, Optional

class InitializeRequest(BaseModel):
  plane_id: str
  flight_id: str
  flight_type: str
  flight_status: str
  plane_parking: str
  min_required_fuel: int = 3000

class FuelRequest(BaseModel):
  plane_id: str
  amount: int

class PassengersRequest(BaseModel):
  plane_id: str
  passengers: List[str]

class FoodRequest(BaseModel):
  plane_id: str
  food: Dict[str, int]

class BaggageRequest(BaseModel):
  plane_id: str
  baggage: List[str]

class TakeoffRequest(BaseModel):
  plane_id: str

class PlaneInfoResponse(BaseModel):
  plane_id: str
  flight_id: str
  flight_type: str
  flight_status: str
  planeParking: str
  currentFuel: int
  minRequiredFuel: int
  maxFuel: int
  maxCapacity: int
