import uvicorn
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import uuid
import random
import logging

# Здесь уже подключается ваш основной код, например, база данных пассажиров, модели и т.д.
# Для примера создадим минимальную in-memory базу
passengers_db = {}

# Пример структуры пассажира (можете заменить на свою модель)
class Passenger:
    def __init__(self, id: str, name: str, isVIP: bool = False, ticket: dict = None):
        self.id = id
        self.name = name
        self.isVIP = isVIP
        self.ticket = ticket or {"ticketId": str(uuid.uuid4())}

# Создадим пару тестовых пассажиров
for _ in range(3):
    pid = str(uuid.uuid4())
    passengers_db[pid] = Passenger(id=pid, name=f"Пассажир {random.randint(1, 100)}")

# Настройка логгера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("PassengersAPI")

app = FastAPI(title="Passengers Module UI Example")
templates = Jinja2Templates(directory="templates")

# Главная страница UI с простым меню
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Для примера передаём список пассажиров в шаблон
    return templates.TemplateResponse("index.html", {"request": request, "passengers": list(passengers_db.values())})

# Эндпоинт для установки VIP-статуса
@app.post("/set_vip")
async def set_vip(request: Request, passenger_id: str = Form(...)):
    passenger = passengers_db.get(passenger_id)
    if not passenger:
        raise HTTPException(status_code=404, detail="Пассажир не найден")
    passenger.isVIP = True
    logger.info(f"Пассажиру {passenger.name} (ID: {passenger.id}) установлен VIP-статус")
    return RedirectResponse(url="/", status_code=303)

# Эндпоинт для «подделки» билета (для примера – просто меняем ticketId)
@app.post("/fake_ticket")
async def fake_ticket(request: Request, passenger_id: str = Form(...)):
    passenger = passengers_db.get(passenger_id)
    if not passenger:
        raise HTTPException(status_code=404, detail="Пассажир не найден")
    # Подделка билета – генерируем новый случайный ticketId
    old_ticket = passenger.ticket["ticketId"]
    new_ticket = str(uuid.uuid4())
    passenger.ticket["ticketId"] = new_ticket
    logger.info(f"У пассажира {passenger.name} (ID: {passenger.id}) билет изменён с {old_ticket} на {new_ticket}")
    return RedirectResponse(url="/", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)
