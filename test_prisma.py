import asyncio
import os
from prisma import Prisma
from dotenv import load_dotenv

async def main() -> None:
    # Explicitly load .env to ensure DATABASE_URL is available
    load_dotenv()
    
    db = Prisma()
    try:
        await db.connect()
        print("Successfully connected to the database via Prisma!")
        
        # Check if there are any places
        count = await db.place.count()
        print(f"Total places in DB: {count}")
        
        if count > 0:
            place = await db.place.find_first()
            print(f"Found sample place: {place.name}")
        else:
            print("No places found, creating a dummy record to test write...")
            dummy = await db.place.create(
                data={
                    "name": "Prisma Test Hub",
                    "address": "123 Prisma Lane",
                    "location": "Locality",
                    "category": "Technology",
                }
            )
            print(f"Successfully created dummy place: {dummy.name}")
            
    except Exception as e:
        print(f"Error during verification: {e}")
    finally:
        if db.is_connected():
            await db.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
