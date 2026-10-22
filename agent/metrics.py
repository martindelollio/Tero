import psutil
import time

def get_system_metrics():
    memoria = psutil.virtual_memory()
    disco = psutil.disk_usage('/')
    cpu = psutil.cpu_percent(interval=1)
    uptime = time.time() - psutil.boot_time()
    return{
        
        "ram_percent": memoria.percent,
        "ram_total": round(memoria.total/1e+9, 2),
        "ram_free": round(memoria.available/1e+9,2),
        "cpu_percent": round(cpu,2),
        "disk_total": round(disco.total/1e+9,2),
        "disk_free": round(disco.free/1e+9,2),
        "disk_percent": disco.percent,
        "uptime_seconds": int(uptime)
    }
    
