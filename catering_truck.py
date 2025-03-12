import asyncio
import requests
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Dict, List
import logging
from contextlib import asynccontextmanager

# Конфигурация и URL-ы
GROUND_CONTROL_URL = "http://localhost:5234/v1"
HS_URL = "https://nasty-camels-brush.loca.lt"

# Логирование
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("CateringTruckAPI")


# Модели данных для Catering Truck
class CateringTruckData(BaseModel):
    id: str
    capacity: int
    status: str
    currentLocation: str
    menu: Dict[str, int]


# Модель запроса на движение (с алиасом для поля "from")
class MovementRequest(BaseModel):
    from_: str = Body(..., alias="from")
    to: str


# Модель запроса на загрузку еды
class LoadFoodRequest(BaseModel):
    menu: Dict[str, int]


# Модель запроса на доставку еды
class DeliverFoodRequest(BaseModel):
    planeId: str


# Класс CateringTruck, который соответствует структуре API
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
                f"current_location={self.current_location}, menu={self.menu})")

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


# FastAPI Lifespan (без автоматической инициализации, можно расширять при необходимости)
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Catering Truck module")
    yield
    logger.info("Stopping Catering Truck module")


# Создание FastAPI приложения
app = FastAPI(title="API Catering Truck by Ramazanova Diana", lifespan=lifespan)


# Эндпоинты для Catering Truck API

@app.get("/")
def read_root():
    logger.info("Root endpoint accessed")
    return {"message": "Welcome to API Catering Truck. Use /v1/catering-trucks for operations."}


@app.get("/v1/catering-trucks", response_model=List[CateringTruckData])
def get_all_trucks():
    logger.info("Fetching all catering trucks")
    return [truck.to_dict() for truck in catering_trucks.values()]


@app.get("/v1/catering-trucks/{truck_id}", response_model=CateringTruckData)
def get_truck_by_id(truck_id: str):
    logger.info(f"Fetching truck with id: {truck_id}")
    truck = catering_trucks.get(truck_id)
    if not truck:
        logger.warning(f"Truck {truck_id} not found")
        raise HTTPException(status_code=404, detail="Catering Truck not found")
    return truck.to_dict()


@app.post("/v1/catering-trucks/init", response_model=dict)
def initialize_truck(data: dict):
    """
    Инициализация Catering Truck.
    Тело запроса: { "id": "CT-001", "location": "G11" }
    """
    logger.info(f"Received init request with data: {data}")
    truck_id = data.get("id")
    location = data.get("location")
    if not truck_id or not location:
        logger.error("Missing id or location in request")
        raise HTTPException(status_code=400, detail="Missing id or location")

    try:
        init_response = requests.post(
            f"{GROUND_CONTROL_URL}/vehicles/init",
            json={"vehicles": [truck_id], "nodes": [location]},
            timeout=10
        )
        logger.info(f"Ground Control response: status={init_response.status_code}, body={init_response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to connect to Ground Control: {str(e)}")
        raise HTTPException(status_code=503, detail=f"Ground Control unavailable: {str(e)}")

    if init_response.status_code != 200:
        logger.error(f"Initialization failed with status {init_response.status_code}: {init_response.text}")
        raise HTTPException(status_code=400, detail=f"Failed to initialize truck: {init_response.text}")

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


@app.post("/v1/catering-trucks/{truck_id}/load-food", response_model=dict)
def load_food(truck_id: str, load_req: LoadFoodRequest):
    truck = catering_trucks.get(truck_id)
    if not truck:
        raise HTTPException(status_code=404, detail="Catering Truck not found")
    if truck.status == "busy":
        raise HTTPException(status_code=400, detail="Truck is busy")
    total = sum(load_req.menu.values())
    if total > truck.capacity:
        raise HTTPException(status_code=400, detail="Menu exceeds truck capacity")
    truck.menu = load_req.menu
    logger.info(f"Food loaded into truck {truck_id}")
    return {"success": True, "message": f"Food loaded into Catering Truck {truck_id}"}


@app.post("/v1/catering-trucks/{truck_id}/deliver-food", response_model=dict)
def deliver_food(truck_id: str, deliver_req: DeliverFoodRequest):
    truck = catering_trucks.get(truck_id)
    if not truck:
        raise HTTPException(status_code=404, detail="Catering Truck not found")
    if truck.status == "busy":
        raise HTTPException(status_code=400, detail="Truck is busy")
    # Сброс меню после доставки
    truck.menu = {"chicken": 0, "pork": 0, "fish": 0, "vegetarian": 0}
    truck.status = "free"
    logger.info(f"Food delivered to plane {deliver_req.planeId} by truck {truck_id}")
    # Уведомление Handling Supervisor (если необходимо)
    try:
        hs_response = requests.post(
            f"{HS_URL}/notify/deliver-food",
            json={"truckId": truck_id, "planeId": deliver_req.planeId},
            timeout=10
        )
        logger.info(f"Handling Supervisor notified: {hs_response.status_code}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to notify Handling Supervisor: {str(e)}")
    return {"success": True, "message": f"Food delivered to plane {deliver_req.planeId}"}


async def move_truck(truck_id: str, from_location: str, to_location: str) -> bool:
    """
    Запрашивает путь от Ground Control, затем для каждой промежуточной точки:
      - запрашивает разрешение (GET /vehicles/move_permission, параметры передаются через params),
      - начинает движение (POST /vehicles/move),
      - подтверждает прибытие (POST /vehicles/arrived).
    """
    logger.info(f"Starting move_truck for {truck_id} from {from_location} to {to_location}")
    truck = catering_trucks.get(truck_id)
    if not truck:
        logger.error(f"Truck {truck_id} not found")
        return False

    try:
        path_response = requests.get(f"{GROUND_CONTROL_URL}/map/path/?from={from_location}&to={to_location}",
                                     timeout=10)
        logger.info(f"Path request response: {path_response.status_code} - {path_response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get path from Ground Control: {str(e)}")
        return False

    if path_response.status_code != 200:
        logger.error(f"Failed to get path: {path_response.text}")
        return False

    path = path_response.json().get("path", [])
    if not path or len(path) < 2:
        logger.error("Empty or invalid path received")
        return False

    logger.info(f"Path received: {path}")
    current = from_location
    for next_point in path[1:]:
        # Запрашиваем разрешение на движение до следующей точки (передаём guid в query string)
        while True:
            try:
                perm_response = requests.get(
                    f"{GROUND_CONTROL_URL}/vehicles/move_permission",
                    params={"guid": truck_id, "vehicleType": "car", "from": current, "to": next_point},
                    timeout=10
                )
                logger.info(f"Move permission response: {perm_response.status_code} - {perm_response.text}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to get move permission: {str(e)}")
                return False

            if perm_response.status_code == 200 and perm_response.json().get("allowed"):
                logger.info(f"Permission granted from {current} to {next_point}")
                break
            logger.info(f"Move permission not granted from {current} to {next_point}, retrying in 5 seconds...")
            await asyncio.sleep(5)

        # Начинаем движение
        try:
            move_response = requests.post(
                f"{GROUND_CONTROL_URL}/vehicles/move",
                json={"guid": truck_id, "vehicleType": "car", "from": current, "to": next_point},
                timeout=10
            )
            logger.info(f"Move response: {move_response.status_code} - {move_response.text}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to start move: {str(e)}")
            return False

        if move_response.status_code != 200:
            logger.error(f"Failed to start move: {move_response.text}")
            return False

        # Подтверждаем прибытие
        try:
            arrived_response = requests.post(
                f"{GROUND_CONTROL_URL}/vehicles/arrived",
                json={"guid": truck_id, "vehicleType": "car", "from": current, "to": next_point},
                timeout=10
            )
            logger.info(f"Arrived response: {arrived_response.status_code} - {arrived_response.text}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to confirm arrival: {str(e)}")
            return False

        if arrived_response.status_code != 200:
            logger.error(f"Failed to confirm arrival: {arrived_response.text}")
            return False

        truck.current_location = next_point
        current = next_point
        logger.info(f"Truck {truck_id} moved to {current}")

    return True


@app.post("/v1/catering-trucks/{truck_id}/move", response_model=dict)
async def start_move(truck_id: str, move_req: MovementRequest):
    truck = catering_trucks.get(truck_id)
    if not truck:
        raise HTTPException(status_code=404, detail="Catering Truck not found")
    if truck.status == "busy":
        raise HTTPException(status_code=400, detail="Truck is busy")
    truck.status = "busy"
    success = await move_truck(truck_id, move_req.from_, move_req.to)
    truck.status = "free"
    if success:
        return {"success": True, "message": f"Catering Truck {truck_id} moved from {move_req.from_} to {move_req.to}"}
    else:
        raise HTTPException(status_code=400, detail=f"Failed to move Catering Truck {truck_id}")


# Запуск приложения
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("catering_truck:app", host="localhost", port=8008, reload=True)
