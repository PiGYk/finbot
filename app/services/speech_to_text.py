import logging
from typing import Optional
import httpx
import io

logger = logging.getLogger("finstack")


class SpeechToTextService:
    """OpenAI Whisper API для розпізнавання голосу."""
    
    def __init__(self, api_key: str, model: str = "whisper-1"):
        self.api_key = api_key
        self.model = model
        self.api_url = "https://api.openai.com/v1/audio/transcriptions"
    
    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        audio_filename: str = "audio.ogg",
        language: str = "uk"  # Ukrainian
    ) -> Optional[str]:
        """
        Розпізнати голос з аудіофайлу.
        
        Args:
            audio_bytes: Байти аудіофайлу
            audio_filename: Ім'я файлу (для типізації)
            language: Код мови (uk, en, тощо)
        
        Returns:
            Розпізнаний текст або None якщо помилка
        """
        
        if not audio_bytes:
            logger.warning("Audio bytes empty")
            return None
        
        logger.debug(f"Transcribing audio: {len(audio_bytes)} bytes, language: {language}")
        
        try:
            # Підготувати файл
            files = {
                "file": (audio_filename, io.BytesIO(audio_bytes), "audio/ogg"),
            }
            
            # Промпт для покращення точності розпізнавання фінансових термінів
            prompt = (
                "Фінансовий бот розпізнає транзакції. Часті слова: "
                "продукти, овочі, фрукти, м'ясо, молоко, яйця, хліб, вода, "
                "фастфуд, піца, бургер, чебуреки, кафе, ресторан, аптека, "
                "транспорт, таксі, бензин, комунальні, інтернет, мобільний, "
                "одяг, взуття, косметика, гігієна, тварини, розваги, подарунки, "
                "спорт, медицина, освіта, дім, ремонт, побутова техніка, "
                "гривень, доларів, євро, готівка, картка, переказ"
            )
            
            data = {
                "model": self.model,
                "language": language,
                "temperature": 0,  # Детермінований результат
                "prompt": prompt,  # Підказка для кращого розпізнавання
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
            }
            
            # Викликати Whisper API
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    self.api_url,
                    files=files,
                    data=data,
                    headers=headers
                )
            
            if response.status_code != 200:
                logger.error(
                    f"Whisper API error {response.status_code}: {response.text}"
                )
                return None
            
            result = response.json()
            text = result.get("text", "").strip()
            
            if not text:
                logger.warning("Whisper returned empty transcription")
                return None
            
            logger.debug(f"✅ Transcription successful: {text[:100]}...")
            return text
        
        except Exception as e:
            logger.error(f"❌ Transcription failed: {repr(e)}")
            return None
    
    async def transcribe_from_url(
        self,
        file_url: str,
        language: str = "uk"
    ) -> Optional[str]:
        """
        Завантажити аудіо з URL та розпізнати.
        
        Args:
            file_url: URL аудіофайлу
            language: Код мови
        
        Returns:
            Розпізнаний текст або None
        """
        try:
            # Завантажити файл
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(file_url)
                response.raise_for_status()
                audio_bytes = response.content
            
            # Розпізнати
            return await self.transcribe_audio(
                audio_bytes,
                audio_filename="audio.ogg",
                language=language
            )
        
        except Exception as e:
            logger.error(f"❌ Failed to download/transcribe from URL: {repr(e)}")
            return None


__all__ = ["SpeechToTextService"]
