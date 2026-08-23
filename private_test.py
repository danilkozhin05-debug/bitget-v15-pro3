import asyncio
import aiohttp
from bot import CFG, Bitget

async def main():
    print('=== BITGET PRIVATE API TEST (NO ORDERS) ===')
    CFG.live=True; CFG.auto_trade=True
    CFG.api_key=input('Bitget API Key (visible): ').strip()
    CFG.api_secret=input('Bitget Secret Key (visible): ').strip()
    CFG.passphrase=input('Bitget Passphrase (visible): ').strip()
    if not all([CFG.api_key,CFG.api_secret,CFG.passphrase]):
        raise SystemExit('Missing credentials')
    bg=Bitget(CFG)
    async with aiohttp.ClientSession() as session:
        data=await bg.account(session, CFG.symbols[0])
        print('PRIVATE API: OK')
        print('Symbol:', CFG.symbols[0])
        print('Available:', (data or {}).get('available'))
        pos=await bg.positions(session)
        print('Positions endpoint: OK | rows:', len(pos or []))
    print('NO ORDER WAS SENT.')

if __name__=='__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        print('PRIVATE API TEST FAILED:', e)
        print('NO ORDER WAS SENT.')
        raise SystemExit(1)
