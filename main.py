import os
from fastapi import FastAPI, Request, BackgroundTasks
from dotenv import load_dotenv
from supabase import create_client, Client
from google import genai
from google.genai import types
import httpx

load_dotenv()

app = FastAPI()

# Configuración de Clientes
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
INSTANCE_NAME = os.getenv("INSTANCE_NAME")

from prompts import SYSTEM_PROMPT_BASE

async def process_and_respond(sender_phone: str, user_message: str):
    supabase.table("clientes").upsert({"telefono": sender_phone}).execute()
    
    supabase.table("historial_chat").insert({
        "telefono": sender_phone,
        "rol": "user",
        "contenido": user_message
    }).execute()
    
    history_data = supabase.table("historial_chat")\
        .select("rol, contenido")\
        .eq("telefono", sender_phone)\
        .order("id", desc=True)\
        .limit(6)\
        .execute().data
    
    history_data.reverse()
    
    formatted_contents = []
    for msg in history_data:
        formatted_contents.append(
            types.Content(
                role="user" if msg["rol"] == "user" else "model",
                parts=[types.Part.from_text(text=msg["contenido"])]
            )
        )

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT_BASE,
        temperature=0.3,
    )
    
    response = gemini_client.models.generate_content(
        model='gemini-2.0-flash',
        contents=formatted_contents,
        config=config
    )
    
    ai_reply = response.text

    supabase.table("historial_chat").insert({
        "telefono": sender_phone,
        "rol": "assistant",
        "contenido": ai_reply
    }).execute()

    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY}
    payload = {
        "number": sender_phone,
        "text": ai_reply
    }
    
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload, headers=headers)

@app.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    try:
        message_data = data.get("data", {})
        key = message_data.get("key", {})
        
        from_me = key.get("fromMe", False)
        sender_phone = key.get("remoteJid", "").split("@")[0]
        
        if from_me or "@g.us" in key.get("remoteJid", ""):
            return {"status": "ignored"}
            
        message_text = (
            message_data.get("message", {}).get("conversation") or 
            message_data.get("message", {}).get("extendedTextMessage", {}).get("text")
        )
        
        if message_text and sender_phone:
            background_tasks.add_task(process_and_respond, sender_phone, message_text)
            return {"status": "processing"}
            
    except Exception as e:
        print(f"Error procesando webhook: {e}")
        
    return {"status": "ok"}