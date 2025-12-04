"""Системные промпты для генерации контента и изображений"""

# Системный промпт для Gemini-3-PRO (генерация контента)
GEMINI_SYSTEM_PROMPT = """Ты — профессиональный контент-маркетолог и эксперт-психолог, специализирующийся на создании вирусных экспертных каруселей для Instagram.

ТВОЯ ЗАДАЧА:
На основе темы, которую пришлет пользователь, создать структуру и контент для карусели из ровно 8 слайдов.

ФОРМАТ ВЫВОДА:
Строгий JSON. Никакого markdown, никаких объяснений до или после JSON.

ТРЕБОВАНИЯ К КОНТЕНТУ:
1. Тон: эмпатичный, профессиональный, бережный, без "успешного успеха".
2. Структура:
   - Слайд 1: Цепляющий заголовок + интригующий подзаголовок.
   - Слайды 2-7: Раскрытие темы. Короткие тезисы. Легко читаемый текст. Буллиты.
   - Слайд 8: Вывод + CTA (Call to Action).
3. Визуальный стиль (описать в JSON):
   - Слайд 1: Фон - фото женщины (файл background/image1.jpg), слегка заблюренное.
   - Слайды 2-8: Светлый фон с легкой текстурой, минимализм (файл background/image2.jpg).

СТРУКТУРА JSON:
{
  "meta_info": {
    "topic": "Тема запроса",
    "platform": "Instagram Carousel",
    "total_slides": 8,
    "overall_concept": {
      "style": "Единый стиль для слайдов 2-8: светлый фон...",
    }
  },
  "slides": [
    {
      "slide_number": 1,
      "type": "cover",
      "title": "Текст заголовка",
      "subtitle": "Текст подзаголовка",
      "visual_idea": "background_image: 'background/image1.jpg'. Blur background..."
    },
    {
      "slide_number": 2,
      "title": "Заголовок слайда",
      "content": ["Тезис 1", "Тезис 2"],
      "background_style": "uniform light textured background (reference: background/image2.jpg)...",
      "decoration": "small illustration description..."
    },
    // ... слайды 3-7 ...
    {
      "slide_number": 8,
      "type": "final",
      "title": "Вывод",
      "content": ["Итог"],
      "call_to_action": "Вопрос к аудитории или призыв",
      "background_style": "...",
      "decoration": "..."
    }
  ]
}"""


def get_image_prompt_slide1(title: str, subtitle: str, visual_idea: str) -> str:
    """Формирует промпт для генерации первого слайда (обложка)"""
    return f"""Create a 4:5 Instagram slide. Use the provided reference image (background/image1.jpg) as the background. Apply a soft blur to the background image to make text readable.

Overlay the following text in Russian:
Title: "{title}" (Font: Elegant Serif, Color: Beige/Light Brown, Large size, Centered).
Subtitle: "{subtitle}" (Font: Sans Serif, Color: White or Dark Brown, Medium size).

Visual idea: {visual_idea}

Atmosphere: Psychological, calm, professional, cozy."""


def get_image_prompt_slides_2_7(title: str, content: list, background_style: str, decoration: str) -> str:
    """Формирует промпт для генерации слайдов 2-7"""
    content_text = "\n".join([f"• {item}" for item in content])
    
    return f"""Create a 4:5 Instagram slide. Use the provided reference image (background/image2.jpg) as the background style.

Background: {background_style}

Design elements:
1. Header: "{title}" (Font: Elegant Serif, Color: Brown/Beige, Top aligned).
2. Body Text: "{content_text}" (Font: Clean Sans Serif, Color: Black/Dark Grey, Aligned left or center, Bullet points).
3. Decor: Place a small, minimalist illustration in the bottom right corner depicting: {decoration}. Style of illustration: Line art or soft watercolor, matching the background.

Keep the design clean, airy, easy to read."""


def get_image_prompt_slide8(title: str, content: list, call_to_action: str, background_style: str, decoration: str) -> str:
    """Формирует промпт для генерации последнего слайда (8) с CTA"""
    content_text = "\n".join([f"• {item}" for item in content])
    
    return f"""Create a 4:5 Instagram slide. Use the provided reference image (background/image2.jpg) as the background style.

Background: {background_style}

Design elements:
1. Header: "{title}" (Font: Elegant Serif, Color: Brown/Beige, Top aligned).
2. Body Text: "{content_text}" (Font: Clean Sans Serif, Color: Black/Dark Grey, Aligned left or center, Bullet points).
3. Decor: Place a small, minimalist illustration in the bottom right corner depicting: {decoration}. Style of illustration: Line art or soft watercolor, matching the background.
4. Footer: Call to Action text: "{call_to_action}" (Font: Sans Serif, Color: Dark Brown, Medium size, Centered at bottom).

Keep the design clean, airy, easy to read."""

