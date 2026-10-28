# 🏰 Monitor de Entradas - Castelo de Soutomaior

Script automatizado en Python diseñado para ejecutarse diariamente, comparar el catálogo de entradas y apartados disponibles en [tickets.castelodesoutomaior.com](https://tickets.castelodesoutomaior.com/) frente al día anterior y disparar alertas inmediatas vía Telegram cuando aparecen novedades.

---

## 🚀 Características

- **Extracción de alto rendimiento**: Conexión directa y autenticada con la API oficial del catálogo (Clorian Backend). Si la API falla o responde algo inesperado, el script termina con error (código 1) sin tocar el estado.
- **Diferencia de conjuntos**: Comparación exacta contra el estado registrado el día anterior (`estado_anterior.json`).
- **Sistema de alertas en Telegram**: Envío de avisos instantáneos con los nombres de las nuevas entradas, precios base y enlace directo a la web oficial de compra.
- **Escritura atómica**: Evita la corrupción del estado local ante caídas de luz o fallos de red durante el guardado.
- **Resiliencia y reintentos**: Estrategia de reintentos con *exponential backoff* para absorber micro-caídas del servidor o errores 5xx/429.
- **Registro de auditoría**: Historial completo de ejecuciones y eventos guardado en `monitor.log`.

---

## 📦 Instalación

1. Clona el repositorio:
   ```bash
   git clone https://github.com/franfolgar/castelo-soutomaior-monitor.git
   cd castelo-soutomaior-monitor
   ```

2. Instala las dependencias necesarias:
   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙️ Configuración (.env)

Copia el archivo de plantilla `.env.example` como `.env`:

```bash
cp .env.example .env
```

Edita los valores en `.env`:

```env
TARGET_URL=https://tickets.castelodesoutomaior.com/
TELEGRAM_TOKEN=tu_token_aqui
TELEGRAM_CHAT_ID=tu_chat_id_aqui
```

---

## 🏃 Ejecución manual de prueba

Para comprobar que todo funciona:

```bash
python monitor.py
```

Al ejecutarse por primera vez:
1. Conectará con la web y obtendrá las entradas activas.
2. Guardará el inventario base en `estado_anterior.json`.
3. Enviará un mensaje de confirmación a tu Telegram con el inventario inicial registrado.

---

## ⏰ Programación automática

### Opción 1: En Windows (Programador de Tareas)

#### Ejecución cada hora (Recomendado para avisos casi al momento):
```powershell
schtasks /create /tn "CasteloSoutomaiorMonitor" /tr "C:\ruta\a\castelo-soutomaior-monitor\run_monitor.bat" /sc hourly /mo 1 /f
```

#### Ejecución diaria única (a las 13:00):
```powershell
schtasks /create /tn "CasteloSoutomaiorMonitor" /tr "C:\ruta\a\castelo-soutomaior-monitor\run_monitor.bat" /sc daily /st 13:00 /f
```

---

### Opción 2: En Linux (mediante `cron`)

1. Abre el editor de tareas programadas del usuario:
   ```bash
   crontab -e
   ```

2. Añade la directiva según la frecuencia deseada:
   - **Cada hora**:
     ```cron
     0 * * * * cd /ruta/al/proyecto/castelo-soutomaior-monitor && /usr/bin/python3 monitor.py >> monitor.log 2>&1
     ```
   - **Diario a las 13:00**:
     ```cron
     0 13 * * * cd /ruta/al/proyecto/castelo-soutomaior-monitor && /usr/bin/python3 monitor.py >> monitor.log 2>&1
     ```

*(Recuerda reemplazar `/ruta/al/proyecto/` y `/usr/bin/python3` por tus rutas absolutas reales, obtenidas con `pwd` y `which python3`)*.
