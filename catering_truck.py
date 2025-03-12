import asyncio
import requests
import uuid
import pika
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Optional
import logging
from contextlib import asynccontextmanager

# Настройка логгера
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("CateringTruckAPI")

# URL других сервисов
GROUND_CONTROL_URL = "http://172.20.10.2:8001/v1"  # Ground Control
HANDLING_SUPERVISOR_URL = "http://172.20.10.2:8002/v1"  # Handling Supervisor
CHECKIN_API_URL = "http://172.20.10.2:8006/v1"  # Check-In

# RabbitMQ конфигурация
RABBITMQ_HOST = "172.20.10.2"
RABBITMQ_QUEUE = "tasks.catering"


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
        self.base_location = "CS-1"  # База по умолчанию (cateringService)

    def __repr__(self):
        return f"CateringTruck(id={self.id}, capacity={self.capacity}, status={self.status}, location={self.current_location}, menu={self.menu})"

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

# FastAPI приложение
app = FastAPI(title="Catering Truck API")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск модуля Catering Truck")
    # Подключение к RabbitMQ
    loop = asyncio.get_event_loop()
    asyncio.ensure_future(start_rabbitmq_consumer(), loop=loop)
    yield
    logger.info("Остановка модуля Catering Truck")


app = FastAPI(title="Catering Truck API", lifespan=lifespan)


# Эндпоинты API
@app.get("/v1/catering-trucks", response_model=list[CateringTruckData])
def get_all_trucks():
    return [truck.to_dict() for truck in catering_trucks.values()]


@app.get("/v1/catering-trucks/{id}", response_model=CateringTruckData)
def get_truck_by_id(id: str):
    truck = catering_trucks.get(id)
    if not truck:
        raise HTTPException(status_code=404, detail="Catering Truck not found")
    return truck.to_dict()


@app.post("/v1/catering-trucks/init", response_model=dict)
def initialize_truck(data: dict):
    truck_id = data.get("id")
    location = data.get("location")
    if not truck_id or not location:
        raise HTTPException(status_code=400, detail="Missing id or location")

    # Инициализация через Ground Control
    response = requests.post(
        f"{GROUND_CONTROL_URL}/vehicles/init",
        json={"vehicles": [truck_id], "nodes": [location]}
    )
    if response.status_code != 300:
        raise HTTPException(status_code=400, detail=f"Failed to initialize truck: {response.text}")

    truck = CateringTruck(
        id=truck_id,
        capacity=100,  # Константа
        status="free",
        current_location=location,
        menu={"chicken": 0, "pork": 0, "fish": 0, "vegetarian": 0}
    )
    catering_trucks[truck_id] = truck
    return {"success": True, "message": f"Catering Truck {truck_id} initialized at {location}"}


@app.post("/v1/catering-trucks/{id}/load-food", response_model=dict)
def load_food(id: str, data: dict):
    truck = catering_trucks.get(id)
    if not truck:
        raise HTTPException(status_code=404, detail="Catering Truck not found")
    if truck.status == "busy":
        raise HTTPException(status_code=400, detail="Truck is busy")

    menu = data.get("menu")
    if not menu:
        raise HTTPException(status_code=400, detail="Menu is required")

    total = sum(menu.values())
    if total > truck.capacity:
        raise HTTPException(status_code=400, detail="Menu exceeds truck capacity")

    truck.menu = menu
    return {"success": True, "message": f"Food loaded into Catering Truck {id}"}


@app.post("/v1/catering-trucks/{id}/deliver-food", response_model=dict)
def deliver_food(id: str, data: dict):
    truck = catering_trucks.get(id)
    if not truck:
        raise HTTPException(status_code=404, detail="Catering Truck not found")
    if truck.status == "busy":
        raise HTTPException(status_code=400, detail="Truck is busy")

    plane_id = data.get("planeId")
    if not plane_id:
        raise HTTPException(status_code=400, detail="planeId is required")

    # Здесь должна быть логика доставки (движение к самолету)
    # Предполагается, что это вызывается после прибытия к точке назначения
    truck.menu = {"chicken": 0, "pork": 0, "fish": 0, "vegetarian": 0}  # Еда доставлена
    truck.status = "free"
    return {"success": True, "message": f"Food delivered to plane {plane_id}"}


# Вспомогательные функции для движения
async def move_truck(truck_id: str, from_location: str, to_location: str):
    truck = catering_trucks.get(truck_id)
    if not truck:
        logger.error(f"Truck {truck_id} not found")
        return False

    # Запрос пути
    response = requests.get(f"{GROUND_CONTROL_URL}/map/path/?from={from_location}&to={to_location}")
    if response.status_code != 200:
        logger.error(f"Failed to get path: {response.text}")
        return False
    path = response.json()["path"]

    # Пошаговое движение по маршруту
    current = from_location
    for next_point in path[1:]:
        # Запрос разрешения
        while True:
            response = requests.get(
                f"{GROUND_CONTROL_URL}/vehicles/move_permission",
                json={"guid": truck_id, "vehicleType": "car", "from": current, "to": next_point}
            )
            if response.status_code == 200 and response.json()["allowed"]:
                break
            await asyncio.sleep(5)  # Ждем 5 секунд перед повторным запросом

        # Начало движения
        response = requests.post(
            f"{GROUND_CONTROL_URL}/vehicles/move",
            json={"guid": truck_id, "vehicleType": "car", "from": current, "to": next_point}
        )
        if response.status_code != 300:
            logger.error(f"Failed to start move: {response.text}")
            return False

        # Подтверждение прибытия
        response = requests.post(
            f"{GROUND_CONTROL_URL}/vehicles/arrived",
            json={"guid": truck_id, "vehicleType": "car", "from": current, "to": next_point}
        )
        if response.status_code != 300:
            logger.error(f"Failed to confirm arrival: {response.text}")
            return False

        truck.current_location = next_point
        current = next_point

    return True


# Обработка задач из RabbitMQ
async def process_task(ch, method, properties, body):
    task = json.loads(body)
    task_id = task["taskId"]
    truck_id = task.get("carId")

    if not truck_id or truck_id not in catering_trucks:
        # Выбираем свободную машину
        for truck in catering_trucks.values():
            if truck.status == "free":
                truck_id = truck.id
                break
        if not truck_id:
            logger.error("No free trucks available")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return

    truck = catering_trucks[truck_id]
    truck.status = "busy"

    # Присвоение задачи
    requests.put(
        f"{HANDLING_SUPERVISOR_URL}/tasks/{task_id}/assign",
        json={"carId": truck_id}
    )

    # Обновление статуса на "inProgress"
    requests.put(
        f"{HANDLING_SUPERVISOR_URL}/tasks/{task_id}",
        json={"state": "inProgress", "stateMessage": "Moving to catering service"}
    )

    # Движение к точке загрузки еды
    take_from = task["details"]["takeFrom"]
    if truck.current_location != take_from:
        success = await move_truck(truck_id, truck.current_location, take_from)
        if not success:
            truck.status = "free"
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return

    # Загрузка еды (получаем меню из Check-In)
    flight_id = task["flightId"]
    response = requests.get(f"{CHECKIN_API_URL}/checkin/{flight_id}/menu")
    if response.status_code != 200:
        logger.error(f"Failed to get menu from Check-In: {response.text}")
        truck.status = "free"
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        return
    menu = response.json()["menuSummary"]
    truck.menu = menu

    # Движение к точке доставки
    point = task["point"]
    success = await move_truck(truck_id, truck.current_location, point)
    if not success:
        truck.status = "free"
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        return

    # Доставка еды
    requests.post(
        f"{app.url_path_for('deliver_food', id=truck_id)}",
        json={"planeId": task["planeId"]}
    )

    # Завершение задачи
    requests.put(f"{HANDLING_SUPERVISOR_URL}/tasks/{task_id}/complete")
    truck.status = "free"

    # Возврат на базу
    await move_truck(truck_id, truck.current_location, truck.base_location)
    ch.basic_ack(delivery_tag=method.delivery_tag)

@app.post("/v1/catering/order", response_model=dict)
def receive_menu_order(data: dict):
    flight_id = data.get("flightId")
    menu = data.get("menu")
    if not flight_id or not menu:
        raise HTTPException(status_code=400, detail="Missing flightId or menu")
    # Здесь можно сохранить данные или сразу использовать для задачи
    logger.info(f"Received menu order for flight {flight_id}: {menu}")
    return {"success": True, "message": f"Menu order received for {flight_id}"}

async def start_rabbitmq_consumer():
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
    channel = connection.channel()
    channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
    channel.basic_consume(queue=RABBITMQ_QUEUE, on_message_callback=process_task)
    logger.info("Starting RabbitMQ consumer...")
    channel.start_consuming()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("catering_truck:app", host="172.20.10.2", port=8008, reload=True)