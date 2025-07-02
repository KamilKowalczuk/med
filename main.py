import os
from datetime import datetime, timedelta
from pyairtable import Table
import functions_framework

# --- Konfiguracja ---
# Pobieranie kluczy ze zmiennych środowiskowych
# Te zmienne musisz ustawić w panelu Google Cloud Function
API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID")

# Inicjalizacja połączenia z tabelami w Airtable
# Upewnij się, że nazwy tabel są identyczne z Twoimi w Airtable
patients_table = Table(API_KEY, BASE_ID, 'Pacjentki')
attack_list_table = Table(API_KEY, BASE_ID, 'Do oddzwonienia')

# --- Funkcja 1: Główny Asystent Reaktywny ("Usypiający") ---
@functions_framework.http
def main_reactive_handler(request):
    """
    Odpowiednik Scenariusza 1. Wywoływana przez webhook z Airtable przy aktualizacji rekordu.
    Obsługuje logikę archiwizacji, rezygnacji i regułę SIMP.
    """
    # Airtable webhook wysyła ID zmodyfikowanego rekordu w payloadzie
    request_json = request.get_json(silent=True)
    if not request_json or 'record_id' not in request_json:
        return 'Brak ID rekordu w zapytaniu.', 400

    record_id = request_json['record_id']
    print(f"Otrzymano żądanie dla rekordu: {record_id}")

    try:
        # Pobieranie pełnych danych rekordu na podstawie jego ID
        patient_record = patients_table.get(record_id)
        fields = patient_record['fields']
        
        # Pobieranie kluczowych pól, z bezpiecznym dostępem (użycie .get())
        status = fields.get('Status')
        simp_verified = fields.get('Weryfikacja SIMP', False)
        key_action_date_str = fields.get('Data Kluczowej Akcji')

        # --- Logika Biznesowa (Router w kodzie) ---

        # 1. Priorytetowa reguła SIMP
        if (simp_verified and key_action_date_str and 
            status not in ['✅ Zarejestrowana', '🏢 Zapisana gdzie indziej']):
            
            key_action_date = datetime.fromisoformat(key_action_date_str.replace('Z', ''))
            next_permission_date = key_action_date + timedelta(days=730) # 2 lata
            
            patients_table.update(record_id, {
                'Data Następnego Uprawnienia': next_permission_date.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                'Status': '🗄️ Archiwalna',
                'Notatki': f"{fields.get('Notatki', '')}\nSystem ({datetime.now().strftime('%Y-%m-%d')}): Rekord zarchiwizowany (reguła SIMP)."
            })
            print(f"Rekord {record_id} zarchiwizowany (reguła SIMP).")
            return 'OK', 200

        # 2. Główny Router oparty na statusie
        if status in ['✅ Zarejestrowana', '🏢 Zapisana gdzie indziej']:
            if not key_action_date_str:
                print(f"Błąd: Status '{status}' wymaga 'Data Kluczowej Akcji'.")
                return 'Brak daty kluczowej akcji.', 400
                
            key_action_date = datetime.fromisoformat(key_action_date_str.replace('Z', ''))
            next_permission_date = key_action_date + timedelta(days=730) # 2 lata
            
            patients_table.update(record_id, {
                'Data Następnego Uprawnienia': next_permission_date.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                'Status': '🗄️ Archiwalna',
                'Notatki': f"{fields.get('Notatki', '')}\nSystem ({datetime.now().strftime('%Y-%m-%d')}): Rekord zarchiwizowany."
            })
            print(f"Rekord {record_id} zarchiwizowany.")

        elif status == '❌ Zrezygnowała':
            patients_table.update(record_id, {
                'Status': '🗄️ Archiwum - Rezygnacja',
                'Data Następnego Uprawnienia': None # Wyczyszczenie daty
            })
            print(f"Rekord {record_id} oznaczony jako rezygnacja.")
        
        # Inne statusy są ignorowane przez tę funkcję
        else:
            print(f"Status '{status}' nie wymaga akcji w tej funkcji.")

        return 'OK', 200

    except Exception as e:
        print(f"Wystąpił krytyczny błąd: {e}")
        return 'Błąd serwera', 500


# --- Funkcja 2: Proaktywny Asystent "Wybudzający" ---
@functions_framework.http
def daily_awakener_handler(request):
    """
    Odpowiednik Scenariusza 2. Wywoływana przez Google Cloud Scheduler codziennie o 5 rano.
    Wyszukuje pacjentki do "wybudzenia".
    """
    print("Rozpoczynam codzienny proces wybudzania pacjentek.")
    today = datetime.now()
    awaken_date_limit = today + timedelta(days=30)

    # Formuła wyszukująca: Status to 'Archiwalna' i data uprawnienia jest w ciągu najbliższych 30 dni
    formula = f"AND(Status = '🗄️ Archiwalna', IS_BEFORE({{Data Następnego Uprawnienia}}, '{awaken_date_limit.strftime('%Y-%m-%d')}'), IS_AFTER({{Data Następnego Uprawnienia}}, '{today.strftime('%Y-%m-%d')}'))"

    try:
        patients_to_awaken = patients_table.all(formula=formula)
        if not patients_to_awaken:
            print("Nie znaleziono pacjentek do wybudzenia.")
            return 'OK - Brak pacjentek', 200

        for patient in patients_to_awaken:
            record_id = patient['id']
            fields = patient['fields']
            next_permission_date_str = fields.get('Data Następnego Uprawnienia')

            patients_table.update(record_id, {
                'Status': '📞 Do kontaktu',
                'Data Następnego Uprawnienia': None, # Czyszczenie daty po wybudzeniu
                'Notatki': f"{fields.get('Notatki', '')}\nSystem ({today.strftime('%Y-%m-%d')}): Rekord automatycznie przywrócony z archiwum. Termin uprawnienia: {next_permission_date_str}."
            })
            print(f"Wybudzono pacjentkę z rekordu: {record_id}")

        return f"Wybudzono {len(patients_to_awaken)} pacjentek.", 200

    except Exception as e:
        print(f"Wystąpił krytyczny błąd podczas wybudzania: {e}")
        return 'Błąd serwera', 500


# --- Funkcja 3: Menedżer Dynamicznej Listy "Do Ataku" ---
@functions_framework.http
def attack_list_manager(request):
    """
    Odpowiednik Scenariusza 3. Wywoływana przez webhook z Airtable.
    Zarządza dodawaniem i usuwaniem pacjentek z 'Listy Do Ataku'.
    """
    request_json = request.get_json(silent=True)
    if not request_json or 'record_id' not in request_json:
        return 'Brak ID rekordu w zapytaniu.', 400

    record_id = request_json['record_id']
    print(f"Menedżer Listy Do Ataku - otrzymano żądanie dla rekordu: {record_id}")

    try:
        patient_record = patients_table.get(record_id)
        status = patient_record['fields'].get('Status')
        
        # Wyszukaj czy pacjentka jest na liście 'Do Ataku'
        formula = f"{{Link do Pacjentki}} = '{record_id}'"
        existing_entry = attack_list_table.all(formula=formula)

        # Logika DODAWANIA do listy
        if status in ['⏳ Oddzwonić później', '📵 Nie odbiera']:
            if not existing_entry: # Dodaj tylko jeśli jeszcze jej tam nie ma
                attack_list_table.create({
                    'Link do Pacjentki': [record_id],
                    'Status w momencie dodania': status,
                    'Data dodania': datetime.now().strftime('%Y-%m-%dT%H:%M:%S.000Z')
                })
                print(f"Dodano rekord {record_id} do Listy 'Do Ataku'.")
            else:
                print(f"Rekord {record_id} już jest na Liście 'Do Ataku'.")

        # Logika USUWANIA z listy
        else:
            if existing_entry:
                entry_id_to_delete = existing_entry[0]['id']
                attack_list_table.delete(entry_id_to_delete)
                print(f"Usunięto rekord {record_id} z Listy 'Do Ataku'.")
            else:
                print(f"Rekord {record_id} nie jest na Liście 'Do Ataku', brak akcji.")

        return 'OK', 200

    except Exception as e:
        print(f"Wystąpił krytyczny błąd w menedżerze listy: {e}")
        return 'Błąd serwera', 500