import pika
import json
import threading
import time
import requests
import logging

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Конфигурация RabbitMQ
RABBITMQ_URL = "amqp://xnyyznus:OSOOLzaQHT5Ys6NPEMAU5DxTChNu2MUe@hawk.rmq.cloudamqp.com:5672/xnyyznus"
RABBITMQ_QUEUE = "tasks.followMe"

# Конфигурация API
GROUND_CONTROL_URL = "https://short-wings-mate.loca.lt"  # GC
HANDLING_SUPERVISOR_URL = "https://twelve-schools-move.loca.lt"  # HS
# TABLO_URL = "https://shy-lies-see.loca.lt"  # Tablo
PLANE_URL = "https://quick-kiwis-invent.loca.lt"  # Plane


# PASSENGER_SERVICE_URL = "https://stupid-lines-cough.loca.lt"  # PS

class FollowMeCar:
    def __init__(self, car_id, base_location):
        self.car_id = car_id
        self.base_location = base_location
        self.current_location = base_location
        self.is_busy = False
        self.current_task = None
        logging.debug(f"Initialized FollowMeCar: {self.car_id} at base {self.base_location}")

    def assign_task(self, task):
        """Назначение задачи машинке."""
        self.current_task = task
        self.is_busy = True
        logging.info(f"Car {self.car_id} assigned to task {task['taskId']}")

    def complete_task(self):
        """Завершение задачи."""
        self.current_task = None
        self.is_busy = False
        logging.info(f"Car {self.car_id} completed the task")

    def move_to_point(self, target_point):
        """Движение машинки к указанной точке."""
        logging.info(f"Car {self.car_id} moving from {self.current_location} to {target_point}")

        # Запрос маршрута у Ground Control
        path_url = f"{GROUND_CONTROL_URL}/v1/map/path?guid={self.car_id}&from={self.current_location}&to={target_point}"
        logging.debug(f"Requesting path from URL: {path_url}")
        ''' path_response = requests.get(path_url)
        logging.debug(f"Received path response: {path_response.status_code}")
        if path_response.status_code != 200:
            logging.error("Failed to get path from Ground Control")
            raise Exception("Failed to get path from Ground Control")
        # path = path_response.json().get("path", [])
        logging.debug(f"Path obtained: {path}") '''
        time.sleep(7.5)

        # Движение по маршруту
        from_point = self.current_location
        to_point = target_point
        logging.debug(f"Segment from {from_point} to {to_point}")

        # Запрос разрешения на движение
        permission_url = f"{GROUND_CONTROL_URL}/v1/vehicles/move_permission?guid={self.car_id}&from={from_point}&to={to_point}"
        logging.debug(f"Requesting move permission from URL: {permission_url}")
        permission_response = requests.get(permission_url)
        logging.debug(f"Move permission response: {permission_response.status_code}")
        if permission_response.status_code != 200 or not permission_response.json().get("allowed", False):
            logging.error("Movement not allowed")
            raise Exception("Movement not allowed")

        # Начало движения
        move_url = f"{GROUND_CONTROL_URL}/v1/vehicles/move"
        move_payload = {
            "guid": self.car_id,
            "vehicleType": "car",
            "from": from_point,
            "to": to_point
        }
        logging.debug(f"Starting move with payload: {move_payload} to URL: {move_url}")
        move_response = requests.post(move_url, json=move_payload)
        logging.debug(f"Move response: {move_response.status_code}")
        if move_response.status_code != 200:
            logging.error("Failed to start movement")
            raise Exception("Failed to start movement")

        # Имитация движения
        logging.debug("Simulating movement delay (2 seconds)")

        # Подтверждение прибытия
        arrived_url = f"{GROUND_CONTROL_URL}/v1/vehicles/arrived"
        arrived_payload = {
            "guid": self.car_id,
            "vehicleType": "car",
            "from": from_point,
            "to": to_point
        }
        logging.debug(f"Confirming arrival with payload: {arrived_payload} to URL: {arrived_url}")
        arrived_response = requests.post(arrived_url, json=arrived_payload)
        logging.debug(f"Arrival confirmation response: {arrived_response.status_code}")
        if arrived_response.status_code != 200:
            logging.error("Failed to confirm arrival")
            raise Exception("Failed to confirm arrival")

        self.current_location = to_point
        logging.info(f"Car {self.car_id} arrived at {to_point}")

    def follow_plane(self, runway, plane_parking):
        """Сопровождение самолета."""
        logging.info(f"Car {self.car_id} starting follow_plane: runway {runway}, parking {plane_parking}")
        # Движение к взлетно-посадочной полосе
        # self.move_to_point(runway)

        # Получение маршрута до места стоянки
        '''path_url = f"{GROUND_CONTROL_URL}/v1/map/path?guid={self.car_id}&from={runway}&to={plane_parking}"
        logging.debug(f"Requesting path to plane parking from URL: {path_url}")
        path_response = requests.get(path_url)
        logging.debug(f"Received path response: {path_response.status_code}")
        if path_response.status_code != 200:
            logging.error("Failed to get path to plane parking")
            raise Exception("Failed to get path to plane parking")
        path = path_response.json().get("path", [])
        logging.debug(f"Path to plane parking: {path}") 

        # Сопровождение самолета
        for point in path:
            logging.debug(f"Sending follow point {point} to plane")
            follow_url = f"{PLANE_URL}/follow"
            follow_payload = {"point": point}
            follow_response = requests.post(follow_url, json=follow_payload)
            logging.debug(f"Follow response status: {follow_response.status_code}") 
            self.move_to_point(point) '''
        self.move_to_point(plane_parking)

        time.sleep(4)

        logging.info(f"Car {self.car_id} escorted the plane to {plane_parking}")

    def return_to_base(self):
        """Возвращение на базу."""
        logging.info(f"Car {self.car_id} returning to base at {self.base_location}")
        self.move_to_point(self.base_location)
        logging.info(f"Car {self.car_id} returned to base")


# Инициализация машинок
def initialize_cars():
    cars = [
        FollowMeCar(car_id="FM-1", base_location="FS-1"),
    ]
    logging.info("Initializing cars...")
    try:
        init_url = f"{GROUND_CONTROL_URL}/v1/vehicles/init"
        payload = {
            "vehicles": [car.car_id for car in cars],
            "nodes": [car.base_location for car in cars]
        }
        logging.debug(f"Sending initialization request to {init_url} with payload: {payload}")
        response = requests.post(init_url, json=payload)
        logging.debug(f"Initialization response status: {response.status_code}")
        if response.status_code == 200:
            logging.info("Cars initialized successfully")
            return cars
        else:
            logging.error(f"Failed to initialize cars. Status code: {response.status_code}")
            logging.error(f"Response: {response.text}")
            raise Exception("Failed to initialize cars")
    except requests.exceptions.RequestException as e:
        logging.error(f"Network error during car initialization: {e}")
        raise Exception("Failed to initialize cars due to network error")


# Обработка задач из RabbitMQ
def process_tasks(cars):
    def callback(ch, method, properties, body):
        try:
            task = json.loads(body)
            logging.info(f"Received task: {task}")
        except Exception as e:
            logging.error(f"Failed to decode task: {e}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        # Назначение задачи свободной машинке
        for car in cars:
            if not car.is_busy:
                car.assign_task(task)

                # Обновление статуса задачи в Handling Supervisor
                assign_url = f"{HANDLING_SUPERVISOR_URL}/v1/tasks/{task['taskId']}/assign"
                assign_payload = {"carId": car.car_id}
                logging.debug(f"Updating task assignment at {assign_url} with payload: {assign_payload}")
                assign_response = requests.put(assign_url, json=assign_payload)
                logging.debug(f"Assignment update response: {assign_response.status_code}")

                try:
                    logging.info(f"Car {car.car_id} executing follow_plane for task {task['taskId']}")
                    car.follow_plane(
                        runway=task["details"]["runway"],
                        plane_parking=task["details"]["planeParking"]
                    )
                    complete_url = f"{HANDLING_SUPERVISOR_URL}/v1/tasks/{task['taskId']}/complete"
                    logging.debug(f"Marking task complete at URL: {complete_url}")
                    complete_response = requests.put(complete_url)
                    logging.debug(f"Task complete response: {complete_response.status_code}")
                except Exception as e:
                    logging.error(f"Error during task execution: {e}")
                finally:
                    logging.info(f"Car {car.car_id} returning to base after task {task['taskId']}")
                    car.return_to_base()
                    car.complete_task()
                break

        logging.debug("Acknowledging message from RabbitMQ")
        ch.basic_ack(delivery_tag=method.delivery_tag)

    # Подключение к RabbitMQ
    logging.info(f"Connecting to RabbitMQ at {RABBITMQ_URL}")
    parameters = pika.URLParameters(RABBITMQ_URL)
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()
    channel.queue_declare(queue=RABBITMQ_QUEUE, durable=False)
    channel.basic_consume(queue=RABBITMQ_QUEUE, on_message_callback=callback)

    logging.info("Waiting for tasks...")
    channel.start_consuming()


# Запуск модуля
if __name__ == "__main__":
    try:
        cars = initialize_cars()
        process_tasks(cars)
    except Exception as e:
        logging.error(f"Error in main execution: {e}")
