import logging
import requests
from fastapi import FastAPI, HTTPException
from datamodel import *

import uvicorn


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI()

HANDLING_SUPERVIZOR_URL = "https://fine-schools-find.loca.lt"
GROUND_CONTROL_URL = "https://short-wings-mate.loca.lt"

    
class Plane:
    def __init__(
        self,
        plane_id: str,
        flight_id: str,
        flight_type: str,
        flight_status: str,
        plane_parking: str,
        min_required_fuel: int,
        max_fuel: int,
        max_capacity: int
    ):
        logger.info(f"Creating new plane {plane_id}")
        self.plane_id = plane_id
        self.flight_id = flight_id
        self.flight_type = flight_type
        self.flight_status = flight_status
        self.planeParking = plane_parking
        self.currentFuel = 0
        self.minRequiredFuel = min_required_fuel
        self.maxFuel = max_fuel
        self.maxCapacity = max_capacity
        self.food = {}
        self.baggage = []
        self.status = "created"
        logger.debug(f"Plane created: {self.__dict__}")

    def get_plane(self):
        return {
            "plane_id": self.plane_id,
            "flight_id": self.flight_id,
            "flight_type": self.flight_type,
            "flight_status": self.flight_status,
            "planeParking": self.planeParking,
            "currentFuel": self.currentFuel,
            "minRequiredFuel": self.minRequiredFuel,
            "maxFuel": self.maxFuel,
            "maxCapacity": self.maxCapacity,
            "food": self.food,
            "baggage": self.baggage,
            "status": self.status
        }
    

class Board:
    def __init__(self):
        logger.info("Initializing Board service")
        self.planes: Dict[str, Plane] = {}

    def send_loading_fuel(self, plane_id: str, planeParking: str, requiredFuel: int):
        logger.info(f"Sending loading fuel for plane to handling supervizor {plane_id}")
        data = {'planeId': plane_id, 'planeParking': planeParking, 'fuelAmount': requiredFuel}
        try:
            requests.post(f"{HANDLING_SUPERVIZOR_URL}/v1/tasks/refuel",
                    json=data)
            logger.debug(f"Sent refuel task to queue handling supervizor")
        except Exception as e:
            logger.error(f"Failed to send refuel task: {str(e)}")

    def get_plane(self, plane_id: str) -> Plane:
        logger.info(f"Fetching plane {plane_id}")
        plane = self.planes.get(str(plane_id))
        if not plane:
            logger.error(f"Plane {plane_id} not found")
            raise HTTPException(status_code=404, detail="Plane not found")
        return plane

board = Board()

        
@app.post("/v1/board/initialize")
def initialize_flight(request: InitializeRequest):
    logger.info(f"Initializing flight for plane {request.plane_id}")
    try:
        # Проверяем существование самолета и статус рейса
        if request.plane_id in board.planes:
            existing_plane = board.planes[request.plane_id]
            
            if existing_plane.flight_status == request.flight_status:
                logger.warning(f"Plane {request.plane_id} already exists with status {request.flight_status}")
                return {
                    "status": "error"
                }
            
            # Удаляем самолет если статус изменился
            logger.info(f"Removing old plane {request.plane_id} (status: {existing_plane.flight_status})")
            del board.planes[request.plane_id]
        else:
            if request.flight_type == "depart" and request.flight_status != "Departed":
                requests.post(
                    f"{GROUND_CONTROL_URL}/v1/vehicles/init",
                    json={
                        "vehicles": [request.plane_id],
                        "nodes": [request.plane_parking]
                    }
                )

        # Создаем новый экземпляр самолета
        plane = Plane(
            plane_id=request.plane_id,
            flight_id=request.flight_id,
            flight_type=request.flight_type,
            flight_status=request.flight_status,
            plane_parking=request.plane_parking,
            min_required_fuel=request.min_required_fuel,
            max_fuel=5000,
            max_capacity=300
        )
                
        board.planes[request.plane_id] = plane
        if request.flight_type == "depart" and request.flight_status != "Departed":
            board.send_loading_fuel(request.plane_id, request.min_required_fuel, request.plane_parking)
        
        if request.flight_type == "depart" and request.flight_status == "Departed":
            takeoff_response = requests.get(f"{GROUND_CONTROL_URL}/v1/vehicles/planes/takeoff_permission?guid={request.plane_id}&runway=RW-1").json()
            
            if takeoff_response.get("allowed") != True:
                while takeoff_response.get("allowed") != True:
                    takeoff_response = requests.get(f"{GROUND_CONTROL_URL}/v1/vehicles/planes/takeoff_permission?guid={request.plane_id}&runway=RW-1").json()
                
            move_permission = requests.get(f"{GROUND_CONTROL_URL}/v1/vehicles/move_permission?guid={request.plane_id}&from={request.plane_parking}&to=RW-1").json()
            if move_permission.get("allowed") != True:
                while move_permission.get("allowed") != True:
                    move_permission = requests.get(f"{GROUND_CONTROL_URL}/v1/vehicles/move_permission?guid={request.plane_id}&from={request.plane_parking}&to=RW-1").json()
            
            data = {"guid": request.plane_id, "vehicleType": "plane", "from": request.plane_parking, "to": "RW-1"}
            requests.post(f"{GROUND_CONTROL_URL}/v1/vehicles/move", json = data)
            
            takeoff_response = requests.post(f"{GROUND_CONTROL_URL}/v1/vehicles/planes/takeoff", json={"guid": request.plane_id, "runway": "RW-1"})
            
            logger.info(takeoff_response)

        if request.flight_type == "arrive" and request.flight_status == "SoonArrived":
            land_permission_response = requests.get(f"{GROUND_CONTROL_URL}/v1/vehicles/planes/land_permission?guid={request.plane_id}&runway=RW-1").json()
            if land_permission_response.get("allowed") != True:
                while land_permission_response.get("allowed") != True:
                    land_permission_response = requests.get(f"{GROUND_CONTROL_URL}/v1/vehicles/planes/land_permission?guid={request.plane_id}&runway=RW-1").json()
            
            land_response = requests.post(f"{GROUND_CONTROL_URL}/v1/vehicles/planes/land", json={"guid": request.plane_id, "runway": "RW-1"}).json()
            if land_response.get("success") != True:
                while land_response.get("success") != True:
                    land_response = requests.post(f"{GROUND_CONTROL_URL}/v1/vehicles/planes/land", json={"guid": request.plane_id, "runway": "RW-1"}).json()

            move_permission = requests.get(f"{GROUND_CONTROL_URL}/v1/vehicles/move_permission?guid={request.plane_id}&from={request.plane_parking}&to=RW-1").json()
            if move_permission.get("allowed") != True:
                while move_permission.get("allowed") != True:
                    move_permission = requests.get(f"{GROUND_CONTROL_URL}/v1/vehicles/move_permission?guid={request.plane_id}&from=RW-1&to={request.plane_parking}").json()
            
            data = {"guid": request.plane_id, "vehicleType": "plane", "from": "RW-1", "to": request.plane_parking}
            requests.post(f"{GROUND_CONTROL_URL}/v1/vehicles/move", json = data)
            
            #тут вызвать флолу ми
            
            board.send_loading_fuel(request.plane_id, request.min_required_fuel, request.plane_parking)
                    
        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Initialization failed: {str(e)}", exc_info=True)
        return {
            "status": "fail",
        }
     


@app.get("/v1/board/{plane_id}", response_model=PlaneInfoResponse)
def get_plane_info(plane_id: str):
    logger.info(f"Info request for plane {plane_id}")
    try:
        plane = board.get_plane(plane_id)
        logger.debug(f"Returning plane info: {plane.__dict__}")
        return {
            "plane_id": plane.plane_id,
            "flight_id": plane.flight_id,
            "flight_type": plane.flight_type,
            "flight_status": plane.flight_status,
            "planeParking": plane.planeParking,
            "currentFuel": plane.currentFuel,
            "minRequiredFuel": plane.minRequiredFuel,
            "maxFuel": plane.maxFuel,
            "maxCapacity": plane.maxCapacity
        }
    except Exception as e:
        logger.error(f"Info request failed: {str(e)}")
        raise


logger.info("Starting application server")
uvicorn.run(app, host="0.0.0.0", port=8050)
