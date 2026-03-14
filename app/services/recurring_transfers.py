import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable

logger = logging.getLogger("finstack")


class RecurringTransfersService:
    """Управління регулярними перекасами."""
    
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._load()
    
    def _load(self) -> None:
        """Завантажити регулярні перекази з файлу."""
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.recurring = data.get("recurring", {})
                    logger.debug(f"✅ Завантажено {len(self.recurring)} регулярних переказів")
            except Exception as e:
                logger.error(f"❌ Помилка завантаження: {repr(e)}")
                self.recurring = {}
        else:
            self.recurring = {}
    
    def _save(self) -> None:
        """Зберегти регулярні перекази."""
        try:
            data = {"recurring": self.recurring}
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"✅ Зберегли {len(self.recurring)} регулярних переказів")
        except Exception as e:
            logger.error(f"❌ Помилка збереження: {repr(e)}")
    
    def create(
        self,
        transfer_id: str,
        source_account: str,
        destination_account: str,
        amount: float,
        currency: str,
        frequency: str,  # "daily", "weekly", "monthly"
        time_of_day: str,  # "HH:MM" e.g. "08:00"
        description: str = "Регулярний переказ",
        start_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Створити регулярний переказ.
        
        Args:
            transfer_id: унікальний ID (наприклад: hash(source+dest+amount))
            source_account: рахунок-відправник
            destination_account: рахунок-отримувач
            amount: сума
            currency: валюта
            frequency: "daily", "weekly", або "monthly"
            time_of_day: час (HH:MM)
            description: опис
            start_date: дата початку (YYYY-MM-DD) або None (сьогодні)
        
        Returns:
            Створена конфігурація
        """
        
        if transfer_id in self.recurring:
            raise ValueError(f"Регулярний переказ вже існує: {transfer_id}")
        
        if frequency not in {"daily", "weekly", "monthly"}:
            raise ValueError(f"Невідома частота: {frequency}")
        
        # Перевірити формат часу
        try:
            datetime.strptime(time_of_day, "%H:%M")
        except ValueError:
            raise ValueError(f"Неправильний формат часу: {time_of_day}. Використовуй HH:MM")
        
        start_date = start_date or datetime.now().strftime("%Y-%m-%d")
        
        config = {
            "id": transfer_id,
            "source_account": source_account,
            "destination_account": destination_account,
            "amount": amount,
            "currency": currency,
            "frequency": frequency,
            "time_of_day": time_of_day,
            "description": description,
            "start_date": start_date,
            "active": True,
            "created_at": datetime.now().isoformat(),
            "last_executed": None,
        }
        
        self.recurring[transfer_id] = config
        self._save()
        
        logger.info(
            f"✅ Створено регулярний переказ: {source_account} → {destination_account} "
            f"{amount} {currency} ({frequency} о {time_of_day})"
        )
        
        return config
    
    def get(self, transfer_id: str) -> Optional[Dict[str, Any]]:
        """Отримати регулярний переказ по ID."""
        return self.recurring.get(transfer_id)
    
    def list_active(self) -> List[Dict[str, Any]]:
        """Отримати список активних регулярних переказів."""
        return [t for t in self.recurring.values() if t.get("active")]
    
    def delete(self, transfer_id: str) -> bool:
        """Видалити регулярний переказ."""
        if transfer_id in self.recurring:
            del self.recurring[transfer_id]
            self._save()
            logger.info(f"✅ Видалено регулярний переказ: {transfer_id}")
            return True
        return False
    
    def pause(self, transfer_id: str) -> bool:
        """Зупинити регулярний переказ."""
        if transfer_id in self.recurring:
            self.recurring[transfer_id]["active"] = False
            self._save()
            logger.info(f"⏸️ Зупинено регулярний переказ: {transfer_id}")
            return True
        return False
    
    def resume(self, transfer_id: str) -> bool:
        """Возобновити регулярний переказ."""
        if transfer_id in self.recurring:
            self.recurring[transfer_id]["active"] = True
            self._save()
            logger.info(f"▶️ Возобновлено регулярний переказ: {transfer_id}")
            return True
        return False
    
    def mark_executed(self, transfer_id: str) -> None:
        """Позначити переказ як виконаний."""
        if transfer_id in self.recurring:
            self.recurring[transfer_id]["last_executed"] = datetime.now().isoformat()
            self._save()
    
    def get_due_transfers(self) -> List[Dict[str, Any]]:
        """
        Отримати перекази які потрібно виконати в цей час.
        
        Перевіряє час та частоту, повертає список переказів до виконання.
        """
        due = []
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_date = now.date()
        
        for transfer in self.list_active():
            if transfer["time_of_day"] != current_time:
                continue  # Не час цього переказу
            
            last_exec = transfer.get("last_executed")
            last_exec_date = None
            if last_exec:
                last_exec_date = datetime.fromisoformat(last_exec).date()
            
            frequency = transfer["frequency"]
            
            # Перевірити чи потрібно виконати
            should_execute = False
            
            if frequency == "daily":
                # Виконати кожен день, якщо не виконувався сьогодні
                should_execute = last_exec_date != current_date
            
            elif frequency == "weekly":
                # Виконати один раз на тиждень (у той же день тижня)
                if last_exec_date:
                    days_since = (current_date - last_exec_date).days
                    should_execute = days_since >= 7
                else:
                    should_execute = True  # Перший раз
            
            elif frequency == "monthly":
                # Виконати один раз на місяць (у той же день місяця)
                if last_exec_date:
                    should_execute = (
                        (current_date.month != last_exec_date.month) or
                        (current_date.year != last_exec_date.year)
                    )
                else:
                    should_execute = True  # Перший раз
            
            if should_execute:
                due.append(transfer)
        
        return due


    async def run_forever(
        self,
        firefly_client: Any,  # FireflyClient
        on_transfer_executed: Optional[Callable] = None,
        poll_seconds: int = 60,
    ) -> None:
        """
        Запустити цикл виконання регулярних переказів.
        
        Args:
            firefly_client: FireflyClient для виконання переказів
            on_transfer_executed: callback для кожного виконаного переказу
            poll_seconds: інтервал перевірки (сек)
        """
        logger.info("🔄 Запуск цикла регулярних переказів...")
        
        while True:
            try:
                # Отримати перекази які потрібно виконати
                due_transfers = self.get_due_transfers()
                
                for transfer in due_transfers:
                    try:
                        logger.info(
                            f"▶️ Виконання регулярного переказу: "
                            f"{transfer['source_account']} → {transfer['destination_account']} "
                            f"{transfer['amount']} {transfer['currency']}"
                        )
                        
                        # Створити переказ у Firefly
                        result = await firefly_client.create_transfer({
                            "amount": transfer["amount"],
                            "currency": transfer["currency"],
                            "source_account": transfer["source_account"],
                            "destination_account": transfer["destination_account"],
                            "description": transfer["description"],
                        })
                        
                        # Позначити як виконаний
                        self.mark_executed(transfer["id"])
                        
                        logger.info(f"✅ Регулярний переказ виконаний: {transfer['id']}")
                        
                        # Callback якщо вказаний
                        if on_transfer_executed:
                            await on_transfer_executed(transfer)
                    
                    except Exception as e:
                        logger.error(
                            f"❌ Помилка при виконанні регулярного переказу {transfer['id']}: {repr(e)}"
                        )
                
                # Чекати перед наступною перевіркою
                await asyncio.sleep(poll_seconds)
            
            except Exception as e:
                logger.error(f"❌ Помилка в цикла регулярних переказів: {repr(e)}")
                await asyncio.sleep(poll_seconds)


__all__ = ["RecurringTransfersService"]
