# MacroTrack: Bounded Nutrition & Macro Logger Agent

## 1. Project Overview
MacroTrack to lekki, autonomiczny agent CLI rozwiązujący problem żmudnego, ręcznego logowania posiłków i makroskładników (białko, tłuszcze, węglowodany, kcal). Użytkownik podaje w języku naturalnym luźny opis tego, co zjadł (np. *„zjadłem 200g piersi z kurczaka i 50g ryżu”*), a agent samodzielnie:
1. Sprawdza dotychczasowy stan dziennika dla danego dnia (`DD-MM-YYYY.json`).
2. Weryfikuje wartości odżywcze w lokalnej bazie produktów.
3. W przypadku braku pozycji w pliku lokalnym, autonomicznie odpytuje publiczne API **Open Food Facts**.
4. Przelicza proporcje podanych gramatur, sumuje wartości z dotychczasowym bilansem dnia i zapisuje zaktualizowany plik.
5. Rejestruje pełny audyt operacji (decyzje, wywołania narzędzi, koszty tokenów, opóźnienia) w pliku `trace.jsonl`.

Zadanie to celowo wykracza poza pojedynczą odpowiedź modelu – wynik jednego narzędzia (np. brak produktu w bazie lokalnej lub zawartość pliku z danego dnia) bezpośrednio warunkuje kolejny krok decyzyjny agenta.

---

## 2. Użytkownik i przykładowe zapytania

* **Grupa docelowa:** Osoby kontrolujące bilans kaloryczny i makroskładniki, które chcą rejestrować posiłki jednym zdaniem w terminalu bez ręcznego przeklikiwania aplikacji mobilnych.
* **Przykładowe wejście:**
  > *„Dziś jest 10-10-2026. Na obiad wpadło 200g piersi z kurczaka, 80g ryżu basmati i 15g oliwy z oliwek. Sprawdź mój bilans i zaktualizuj dziennik.”*
  > *„Zjadłem przed chwilą 150g skyru naturalnego. Dopisz to do dzisiaj.”* (gdzie skyr nie istnieje w pliku lokalnym i musi zostać pobrany z sieci).

---

## 3. Kryteria sukcesu (Definition of Done)

Projekt uznaje się za poprawnie wykonany, gdy pojedyncze uruchomienie spełnia poniższe warunki:
1. **Wieloetapowa weryfikacja:** Model nie zgaduje wartości odżywczych „z głowy” – pozyskuje je wyłącznie z narzędzia lokalnego lub zewnętrznego API.
2. **Zachowanie ciągłości danych:** Nowo dodany posiłek zostaje dopisany do listy `meals`, a pole `daily_totals` w pliku `logs/DD-MM-YYYY.json` zawiera faktyczną sumę wcześniejszych posiłków oraz nowego wpisu.
3. **Obsługa braków danych (Fallback Chain):** Przy braku produktu w bazie lokalnej agent nie zawiesza pętli, lecz odpytuje API Open Food Facts i zapisuje nowo pobrany produkt do lokalnego cache (`data/nutrition_db.json`).
4. **Ograniczona pętla (Bounded Execution):** Agent wykonuje maksymalnie zadaną liczbę kroków (`MAX_STEPS = 6`). W przypadku braku możliwości rozstrzygnięcia przerywa działanie z jawnym komunikatem.
5. **Deterministyczny audyt:** Każdy krok wykonania zapisuje rekord w `trace.jsonl` zawierający czas trwania, liczbę tokenów, szacowany koszt w USD oraz argumenty i wyniki narzędzi.

---

## 4. Architektura pętli decyzyjnej

Pętla działa w schemacie ReAct (Reason --> Act --> Observe):

```text
[User Prompt]
      │
      ▼
┌──────────────┐      Tool Call: read_daily_log
│  Agent Loop  │ ───────────────────────────────────► Odczyt dotychczasowego bilansu dnia
└──────────────┘ ◄─────────────────────────────────── (Zwraca dotychczasowe sumy lub nowy dzień)
      │
      ▼
┌──────────────┐      Tool Call: lookup_local_food
│  Agent Loop  │ ───────────────────────────────────► Sprawdzenie lokalnej bazy JSON
└──────────────┘ ◄───────────────────────────────────
      │
      ├── [Produkt znaleziony] ─────────┐
      │                                 │
      └── [Brak w lokalnej bazie]       │
               │                        │
               ▼                        │
         Tool Call:                     │
         query_open_food_facts          │
               │                        │
               ▼                        │
┌──────────────┐                        ▼
│  Agent Loop  │ ◄─── Pobrane dane z API / lokalnej bazy
└──────────────┘
      │
      ▼
┌──────────────┐      Tool Call: save_daily_log
│  Agent Loop  │ ───────────────────────────────────► Przeliczenie porcji, sumowanie
└──────────────┘ ◄─────────────────────────────────── i zapis do logs/DD-MM-YYYY.json
      │
      ▼
[Final Response] (Podsumowanie posiłku + nowy łączny bilans dnia dla użytkownika)
