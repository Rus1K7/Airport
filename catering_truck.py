import asyncio
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict
import logging
from contextlib import asynccontextmanager

# Настройка логгера
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("CateringTruckAPI")

# URL Ground Control
GROUND_CONTROL_URL = "https://afraid-badgers-join.loca.lt/v1"

# Модель данных для Catering Truck
class CateringTruckData(BaseModel):
    id: str
    capacity: int
    status: str  # "free" или "busy"
    current_location: str
    menu: Dict[str, int]  # {"chicken": 0, "pork": 0, "fish": 0, "vegetarian": 0}

# Класс для работы с машиной
class CateringTruck:
    def __init__(self, id: str, capacity: int, status: str, current_location: str, menu: Dict[str, int]):
        self.id = id
        self.capacity = capacity
        self.status = status
        self.current_location = current_location
        self.menu = menu
        self.base_location = "CS-1"

    def __repr__(self):
        return (f"CateringTruck(id={self.id}, capacity={self.capacity}, status={self.status}, "
                f"location={self.current_location}, menu={self.menu})")

    @staticmethod
    def from_dict(data: dict):
        return CateringTruck(
            id=data["id"],
            capacity=data["capacity"],
            status=data["status"],
            current_location=data["currentLocation"],
            menu=data["menu"]
        )

    def to_dict(self):
        return {
            "id": self.id,
            "capacity": self.capacity,
            "status": self.status,
            "currentLocation": self.current_location,
            "menu": self.menu
        }

# In-memory хранилище машин
catering_trucks: Dict[str, CateringTruck] = {}


async def check_available_node(retries=5, delay=10):
    """ Проверяет доступные узлы, повторяя попытки, если они заняты. """
    for attempt in range(retries):
        try:
            logger.info(
                f"Checking available nodes (attempt {attempt + 1}/{retries}) at {GROUND_CONTROL_URL}/nodes/free")
            response = requests.get(f"{GROUND_CONTROL_URL}/nodes/free", timeout=10)
            if response.status_code == 200:
                free_nodes = response.json().get("nodes", [])
                if free_nodes:
                    return free_nodes[0]  # Берём первый доступный узел
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to check available nodes: {str(e)}")

        logger.warning(f"No available nodes, retrying in {delay} seconds...")
        await asyncio.sleep(delay)  # Ждём перед повторной попыткой

    logger.error("No available nodes after multiple retries.")
    return None


# FastAPI приложение
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Catering Truck module")

    # Принудительно используем CS-1, так как он пуст
    free_node = "CS-1"

    truck_id = "CT-1"
    logger.info(f"Initializing truck {truck_id} at {free_node}")

    try:
        response = requests.post(
            f"{GROUND_CONTROL_URL}/vehicles/init",
            json={"vehicles": [truck_id], "nodes": [free_node]},
            timeout=10
        )
        if response.status_code == 200:
            catering_trucks[truck_id] = CateringTruck(
                id=truck_id,
                capacity=100,
                status="free",
                current_location=free_node,
                menu={"chicken": 0, "pork": 0, "fish": 0, "vegetarian": 0}
            )
            logger.info(f"Truck {truck_id} successfully initialized at {free_node}")
        else:
            logger.error(f"Failed to initialize truck {truck_id}: {response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to connect to Ground Control: {str(e)}")

    yield
    logger.info("Stopping Catering Truck module")




app = FastAPI(title="Catering Truck API", lifespan=lifespan)

@app.get("/")
def read_root():
    logger.info("Root endpoint accessed")
    return {"message": "Welcome to Catering Truck API. Use /v1/catering-trucks for operations."}

@app.get("/v1/catering-trucks", response_model=list[CateringTruckData])
def get_all_trucks():
    logger.info("Fetching all catering trucks")
    logger.info(f"Current trucks: {list(catering_trucks.keys())}")
    return [truck.to_dict() for truck in catering_trucks.values()]

@app.get("/v1/catering-trucks/{id}", response_model=CateringTruckData)
def get_truck_by_id(id: str):
    logger.info(f"Fetching truck with id: {id}")
    truck = catering_trucks.get(id)
    if not truck:
        logger.warning(f"Truck {id} not found")
        raise HTTPException(status_code=404, detail="Catering Truck not found")
    logger.info(f"Found truck: {truck}")
    return truck.to_dict()

@app.post("/v1/catering-trucks/init", response_model=dict)
def initialize_truck(data: dict):
    logger.info(f"Received init request with data: {data}")
    truck_id = data.get("id")
    location = data.get("location")
    if not truck_id or not location:
        logger.error("Missing id or location in request")
        raise HTTPException(status_code=400, detail="Missing id or location")

    logger.info(f"Starting initialization for truck {truck_id} at {location}")
    try:
        logger.info(f"Sending request to Ground Control: {GROUND_CONTROL_URL}/vehicles/init")
        response = requests.post(
            f"{GROUND_CONTROL_URL}/vehicles/init",
            json={"vehicles": [truck_id], "nodes": [location]},
            timeout=10
        )
        logger.info(f"Ground Control response: status={response.status_code}, body={response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to connect to Ground Control: {str(e)}")
        raise HTTPException(status_code=503, detail=f"Ground Control unavailable: {str(e)}")

    if response.status_code != 200:  # Попробуем также 300, если 200 не работает
        logger.error(f"Initialization failed with status {response.status_code}: {response.text}")
        raise HTTPException(status_code=400, detail=f"Failed to initialize truck: {response.text}")

    truck = CateringTruck(
        id=truck_id,
        capacity=100,
        status="free",
        current_location=location,
        menu={"chicken": 0, "pork": 0, "fish": 0, "vegetarian": 0}
    )
    catering_trucks[truck_id] = truck
    logger.info(f"Truck {truck_id} successfully initialized at {location}")
    return {"success": True, "message": f"Catering Truck {truck_id} initialized at {location}"}

@app.post("/v1/catering-trucks/{id}/load-food", response_model=dict)
def load_food(id: str, data: dict):
    logger.info(f"Load food request for truck {id} with data: {data}")
    truck = catering_trucks.get(id)
    if not truck:
        logger.warning(f"Truck {id} not found for loading food")
        raise HTTPException(status_code=404, detail="Catering Truck not found")
    if truck.status == "busy":
        logger.warning(f"Truck {id} is busy")
        raise HTTPException(status_code=400, detail="Truck is busy")

    menu = data.get("menu")
    if not menu:
        logger.error("Menu is required for loading food")
        raise HTTPException(status_code=400, detail="Menu is required")

    total = sum(menu.values())
    if total > truck.capacity:
        logger.error(f"Menu exceeds capacity for truck {id}")
        raise HTTPException(status_code=400, detail="Menu exceeds truck capacity")

    truck.menu = menu
    logger.info(f"Food loaded into truck {id}")
    return {"success": True, "message": f"Food loaded into Catering Truck {id}"}

@app.post("/v1/catering-trucks/{id}/deliver-food", response_model=dict)
def deliver_food(id: str, data: dict):
    logger.info(f"Deliver food request for truck {id} with data: {data}")
    truck = catering_trucks.get(id)
    if not truck:
        logger.warning(f"Truck {id} not found for food delivery")
        raise HTTPException(status_code=404, detail="Catering Truck not found")
    if truck.status == "busy":
        logger.warning(f"Truck {id} is busy")
        raise HTTPException(status_code=400, detail="Truck is busy")

    plane_id = data.get("planeId")
    if not plane_id:
        logger.error("planeId is required for delivery")
        raise HTTPException(status_code=400, detail="planeId is required")

    truck.menu = {"chicken": 0, "pork": 0, "fish": 0, "vegetarian": 0}
    truck.status = "free"
    logger.info(f"Food delivered to plane {plane_id} by truck {id}")
    return {"success": True, "message": f"Food delivered to plane {plane_id}"}

async def move_truck(truck_id: str, from_location: str, to_location: str):
    logger.info(f"Starting move_truck for {truck_id} from {from_location} to {to_location}")
    truck = catering_trucks.get(truck_id)
    if not truck:
        logger.error(f"Truck {truck_id} not found")
        return False

    try:
        response = requests.get(f"{GROUND_CONTROL_URL}/map/path/?from={from_location}&to={to_location}", timeout=10)
        logger.info(f"Path request response: {response.status_code} - {response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get path from Ground Control: {str(e)}")
        return False

    if response.status_code != 200:
        logger.error(f"Failed to get path: {response.text}")
        return False
    path = response.json().get("path", [])
    if not path:
        logger.error("Empty path received")
        return False

    logger.info(f"Path received: {path}")
    current = from_location
    for next_point in path[1:]:
        while True:
            try:
                response = requests.get(
                    f"{GROUND_CONTROL_URL}/vehicles/move_permission",
                    json={"guid": truck_id, "vehicleType": "car", "from": current, "to": next_point},
                    timeout=10
                )
                logger.info(f"Move permission response: {response.status_code} - {response.text}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to get move permission: {str(e)}")
                return False

            if response.status_code == 200 and response.json().get("allowed"):
                logger.info(f"Permission granted from {current} to {next_point}")
                break
            logger.info(f"Move permission not granted from {current} to {next_point}, retrying in 5s...")
            await asyncio.sleep(5)

        try:
            response = requests.post(
                f"{GROUND_CONTROL_URL}/vehicles/move",
                json={"guid": truck_id, "vehicleType": "car", "from": current, "to": next_point},
                timeout=10
            )
            logger.info(f"Move response: {response.status_code} - {response.text}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to start move: {str(e)}")
            return False

        if response.status_code != 200:
            logger.error(f"Failed to start move: {response.text}")
            return False

        try:
            response = requests.post(
                f"{GROUND_CONTROL_URL}/vehicles/arrived",
                json={"guid": truck_id, "vehicleType": "car", "from": current, "to": next_point},
                timeout=10
            )
            logger.info(f"Arrived response: {response.status_code} - {response.text}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to confirm arrival: {str(e)}")
            return False

        if response.status_code != 200:
            logger.error(f"Failed to confirm arrival: {response.text}")
            return False

        truck.current_location = next_point
        current = next_point
        logger.info(f"Truck {truck_id} moved to {current}")

    return True

@app.post("/v1/catering-trucks/{id}/test-move", response_model=dict)
async def test_move(id: str, data: dict):
    logger.info(f"Received test-move request for truck {id} with data: {data}")
    to_location = data.get("to_location")
    if not to_location:
        logger.error("to_location is required")
        raise HTTPException(status_code=400, detail="to_location is required")

    truck = catering_trucks.get(id)
    if not truck:
        logger.warning(f"Truck {id} not found for test-move")
        raise HTTPException(status_code=404, detail="Catering Truck not found")

    logger.info(f"Starting test move for truck {id} from {truck.current_location} to {to_location}")
    truck.status = "busy"
    success = await move_truck(id, truck.current_location, to_location)
    truck.status = "free"

    if success:
        logger.info(f"Test move successful for truck {id} to {to_location}")
        return {"success": True, "message": f"Catering Truck {id} moved to {to_location}"}
    else:
        logger.error(f"Test move failed for truck {id} to {to_location}")
        return {"success": False, "message": f"Failed to move Catering Truck {id} to {to_location}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("catering_truck:app", host="localhost", port=8008, reload=True)