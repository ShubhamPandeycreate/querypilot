# Error analysis review sheet

50 failures sampled from `bird_single_shot_ollama_full500.jsonl`, stratified by difficulty, seed 42.

For each one: read the question, compare the two result sets, then write a category in the labels CSV. The **suggested** line is a heuristic to speed up reading. It is not evidence and never appears in the report.

## Categories

- `gold_wrong` — The gold SQL looks wrong or answers a different question
- `ambiguous` — The question admits several honest readings; gold picked one
- `metric_artifact` — Semantically right, scored wrong (column order, extra columns, types)
- `schema_linking` — Wrong tables or columns chosen
- `value_format` — Filter did not match how values are actually stored
- `aggregation` — Wrong grain, join fan-out, wrong GROUP BY or aggregate
- `filter_logic` — Missing, extra or inverted condition
- `order_limit` — Ordering or LIMIT differs from what was asked
- `no_sql` — Model produced no SQL at all
- `exec_error` — SQL was produced but failed to run
- `other` — Anything else; write a note

---

## 1. `bird_1003` (moderate, db `formula_1`)

**Question.** How many accidents did the driver who had the highest number accidents in the Canadian Grand Prix have?

**Evidence hint.** number of accidents refers to the number where statusid = 3; Canadian Grand Prix refers to the race of name

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT COUNT(T1.driverId) FROM results AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId INNER JOIN status AS T3 on T1.statusId = T3.statusId WHERE T3.statusId = 3 AND T2.name = 'Canadian Grand Prix' GROUP BY T1.driverId ORDER BY COUNT(T1.driverId) DESC LIMIT 1
```

**Predicted SQL**

```sql
SELECT MAX(accidents) 
FROM (
    SELECT driverId, COUNT(CASE WHEN statusId = 3 THEN 1 END) AS accidents
    FROM results
    WHERE raceId = (SELECT raceId FROM races WHERE name = 'Canadian Grand Prix')
    GROUP BY driverId
) AS driver_accidents
```

**Gold result**

| COUNT(T1.driverId) |
| --- |
| 2 |

**Predicted result**

| MAX(accidents) |
| --- |
| 1 |

---

## 2. `bird_1011` (challenging, db `formula_1`)

**Question.** Which top 20 driver created the shortest lap time ever record in a Formula_1 race? Please give them full names.

**Evidence hint.** shortest lap time refers to MIN(time); the time format for the shortest lap time is 'MM:SS.mmm' or 'M:SS.mmm'; full name of the driver refers to forename, surname

_suggested: `metric_artifact`_

**Gold SQL**

```sql
WITH lap_times_in_seconds AS (SELECT driverId, (CASE WHEN SUBSTR(time, 1, INSTR(time, ':') - 1) <> '' THEN CAST(SUBSTR(time, 1, INSTR(time, ':') - 1) AS REAL) * 60 ELSE 0 END + CASE WHEN SUBSTR(time, INSTR(time, ':') + 1, INSTR(time, '.') - INSTR(time, ':') - 1) <> '' THEN CAST(SUBSTR(time, INSTR(time, ':') + 1, INSTR(time, '.') - INSTR(time, ':') - 1) AS REAL) ELSE 0 END + CASE WHEN SUBSTR(time, INSTR(time, '.') + 1) <> '' THEN CAST(SUBSTR(time, INSTR(time, '.') + 1) AS REAL) / 1000 ELSE 0 END) AS time_in_seconds FROM lapTimes) SELECT T2.forename, T2.surname, T1.driverId FROM (SELECT driverId, MIN(time_in_seconds) AS min_time_in_seconds FROM lap_times_in_seconds GROUP BY driverId) AS T1 INNER JOIN drivers AS T2 ON T1.driverId = T2.driverId ORDER BY T1.min_time_in_seconds ASC LIMIT 20
```

**Predicted SQL**

```sql
SELECT drivers.forename, drivers.surname
FROM drivers
JOIN lapTimes ON drivers.driverId = lapTimes.driverId
GROUP BY drivers.driverId
ORDER BY MIN(lapTimes.time) ASC
LIMIT 20
```

**Gold result**

| forename | surname | driverId |
| --- | --- | --- |
| Nico | Hülkenberg | 807 |
| Daniel | Ricciardo | 817 |
| Adrian | Sutil | 16 |
| Kevin | Magnussen | 825 |
| Nico | Rosberg | 3 |
| Jules | Bianchi | 824 |
| Jean-Éric | Vergne | 818 |
| Esteban | Gutiérrez | 821 |

_… 12 more rows_

**Predicted result**

| forename | surname |
| --- | --- |
| Anthony | Davidson |
| Enrique | Bernoldi |
| Christian | Klien |
| Olivier | Panis |
| Yuji | Ide |
| Esteban | Tuero |
| Heikki | Kovalainen |
| Mark | Webber |

_… 12 more rows_

---

## 3. `bird_1014` (challenging, db `formula_1`)

**Question.** Please list the lap records for the circuits in Italy.

**Evidence hint.** lap record means the fastest time recorded which refers to time

_suggested: `schema_linking`_

**Gold SQL**

```sql
WITH fastest_lap_times AS (SELECT T1.raceId, T1.FastestLapTime, (CAST(SUBSTR(T1.FastestLapTime, 1, INSTR(T1.FastestLapTime, ':') - 1) AS REAL) * 60) + (CAST(SUBSTR(T1.FastestLapTime, INSTR(T1.FastestLapTime, ':') + 1, INSTR(T1.FastestLapTime, '.') - INSTR(T1.FastestLapTime, ':') - 1) AS REAL)) + (CAST(SUBSTR(T1.FastestLapTime, INSTR(T1.FastestLapTime, '.') + 1) AS REAL) / 1000) as time_in_seconds FROM results AS T1 WHERE T1.FastestLapTime IS NOT NULL ) SELECT T1.FastestLapTime as lap_record FROM results AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId INNER JOIN circuits AS T3 on T2.circuitId = T3.circuitId INNER JOIN (SELECT MIN(fastest_lap_times.time_in_seconds) as min_time_in_seconds FROM fastest_lap_times INNER JOIN races AS T2 on fastest_lap_times.raceId = T2.raceId INNER JOIN circuits AS T3 on T2.circuitId = T3.circuitId WHERE T3.country = 'Italy' ) AS T4 ON (CAST(SUBSTR(T1.FastestLapTime, 1, INSTR(T1.FastestLapTime, ':') - 1) AS REAL) * 60) + (CAST(SUBSTR(T1.FastestLapTime, INSTR(T1.FastestLapTime, ':') + 1, INSTR(T1.FastestLapTime, '.') - INSTR(T1.FastestLapTime, ':') - 1) AS REAL)) + (CAST(SUBSTR(T1.FastestLapTime, INSTR(T1.FastestLapTime, '.') + 1) AS REAL) / 1000) = T4.min_time_in_seconds LIMIT 1
```

**Predicted SQL**

```sql
SELECT 
    circuits.name,
    MIN(lapTimes.time) AS fastest_lap_time
FROM 
    circuits
JOIN 
    races ON circuits.circuitId = races.circuitId
JOIN 
    lapTimes ON races.raceId = lapTimes.raceId
WHERE 
    circuits.country = 'Italy'
GROUP BY 
    circuits.circuitId
```

**Gold result**

| lap_record |
| --- |
| 1:20.411 |

**Predicted result**

| name | fastest_lap_time |
| --- | --- |
| Autodromo Nazionale di Monza | 15:14.219 |
| Autodromo Enzo e Dino Ferrari | 13:29.130 |

---

## 4. `bird_1028` (challenging, db `european_football_2`)

**Question.** In Scotland Premier League, which away team won the most during the 2010 season?

**Evidence hint.** Final result should return the Team.team_long_name; Scotland Premier League refers to League.name = 'Scotland Premier League'; away team refers to away_team_api_id; away team that won the most refers to MAX(SUBTRACT(away_team_goal, home_team_goal) > 0); 2010 season refers to season = '2009/2010'; won the most refers to MAX(COUNT(*));

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT teamInfo.team_long_name FROM League AS leagueData INNER JOIN Match AS matchData ON leagueData.id = matchData.league_id INNER JOIN Team AS teamInfo ON matchData.away_team_api_id = teamInfo.team_api_id WHERE leagueData.name = 'Scotland Premier League' AND matchData.season = '2009/2010' AND matchData.away_team_goal - matchData.home_team_goal > 0 GROUP BY matchData.away_team_api_id ORDER BY COUNT(*) DESC LIMIT 1
```

**Predicted SQL**

```sql
SELECT 
    T.team_long_name
FROM 
    Team T
JOIN 
    Match M ON T.team_api_id = M.away_team_api_id
JOIN 
    League L ON M.league_id = L.id
WHERE 
    L.name = 'Scotland Premier League'
    AND M.season = '2009/2010'
    AND M.away_team_goal > M.home_team_goal
GROUP BY 
    T.team_long_name
ORDER BY 
    COUNT(*) DESC
LIMIT 1
```

**Gold result**

| team_long_name |
| --- |
| Celtic |

**Predicted result**

| team_long_name |
| --- |
| Rangers |

---

## 5. `bird_1036` (challenging, db `european_football_2`)

**Question.** List the long name of teams with above-average build-up play passing in 2012.

**Evidence hint.** long name of teams refers to team_long_name; build-up play passing refers to buildUpPlayPassing; above-average build-up play passing = buildUpPlayPassing > DIVIDE(SUM(buildUpPlayPassing), COUNT(team_long_name) WHERE buildUpPlayPassing IS NOT NULL); in 2012 refers to strftime('%Y', date) = '2012';

_suggested: `no_sql`_

**Gold SQL**

```sql
SELECT DISTINCT t4.team_long_name FROM Team_Attributes AS t3 INNER JOIN Team AS t4 ON t3.team_api_id = t4.team_api_id WHERE SUBSTR(t3.`date`, 1, 4) = '2012' AND t3.buildUpPlayPassing > ( SELECT CAST(SUM(t2.buildUpPlayPassing) AS REAL) / COUNT(t1.id) FROM Team AS t1 INNER JOIN Team_Attributes AS t2 ON t1.team_api_id = t2.team_api_id WHERE STRFTIME('%Y',t2.`date`) = '2012')
```

**Predicted SQL**

```sql
-- none produced
```

**Gold result**

| team_long_name |
| --- |
| Aberdeen |
| AC Ajaccio |
| Milan |
| UD Almería |
| Angers SCO |
| Aston Villa |
| Atalanta |
| Athletic Club de Bilbao |

_… 120 more rows_

**Predicted result**

`no SQL`

---

## 6. `bird_1088` (moderate, db `european_football_2`)

**Question.** Please list the names of the players whose volley score and dribbling score are over 70.

**Evidence hint.** volley score are over 70 refers to volleys > 70; dribbling score refers to dribbling are over 70 refers to dribbling > 70;

_suggested: `aggregation`_

**Gold SQL**

```sql
SELECT DISTINCT t1.player_name FROM Player AS t1 INNER JOIN Player_Attributes AS t2 ON t1.player_api_id = t2.player_api_id WHERE t2.volleys > 70 AND t2.dribbling > 70
```

**Predicted SQL**

```sql
SELECT `Player`.`player_name`
FROM `Player`
JOIN `Player_Attributes` ON `Player`.`player_api_id` = `Player_Attributes`.`player_api_id`
WHERE `Player_Attributes`.`volleys` > 70 AND `Player_Attributes`.`dribbling` > 70
```

**Gold result**

| player_name |
| --- |
| Aaron Hunt |
| Aaron Ramsey |
| Aatif Chahechouhe |
| Abdelkader Ghezzal |
| Abdoulaye Diallo Sadio,22 |
| Abel Hernandez |
| Abou Diaby |
| Abraham Guie Gneki |

_… 1097 more rows_

**Predicted result**

| player_name |
| --- |
| Aaron Hunt |
| Aaron Hunt |
| Aaron Hunt |
| Aaron Hunt |
| Aaron Hunt |
| Aaron Hunt |
| Aaron Hunt |
| Aaron Hunt |

_… 15421 more rows_

---

## 7. `bird_1092` (simple, db `european_football_2`)

**Question.** Give the name of the league had the most matches in the 2008/2009 season?

**Evidence hint.** league that had the most matches in the 2008/2009 season refers to MAX(league_name WHERE season = '2008/2009');

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT t1.name FROM League AS t1 JOIN Match AS t2 ON t1.id = t2.league_id WHERE t2.season = '2008/2009' GROUP BY t1.name HAVING COUNT(t2.id) = (SELECT MAX(match_count) FROM (SELECT COUNT(t2.id) AS match_count FROM Match AS t2 WHERE t2.season = '2008/2009' GROUP BY t2.league_id))
```

**Predicted SQL**

```sql
SELECT League.name
FROM League
JOIN Match ON League.id = Match.league_id
WHERE Match.season = '2008/2009'
GROUP BY League.name
ORDER BY COUNT(*) DESC
LIMIT 1
```

**Gold result**

| name |
| --- |
| England Premier League |
| France Ligue 1 |
| Italy Serie A |
| Spain LIGA BBVA |

**Predicted result**

| name |
| --- |
| Spain LIGA BBVA |

---

## 8. `bird_1135` (moderate, db `european_football_2`)

**Question.** Please provide top four football players' IDs who are among the lowest potential players and prefer to use the right foot when attacking.

**Evidence hint.** lowest potential players refers to MIN(potential); prefer to use the right foot when attacking refers to preferred_foot = 'right'

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT id FROM Player_Attributes WHERE preferred_foot = 'right' ORDER BY potential ASC LIMIT 4
```

**Predicted SQL**

```sql
SELECT `Player`.id
FROM `Player`
JOIN `Player_Attributes` ON `Player`.player_api_id = `Player_Attributes`.player_api_id
WHERE `Player_Attributes`.preferred_foot = 'right'
ORDER BY `Player_Attributes`.potential ASC
LIMIT 4
```

**Gold result**

| id |
| --- |
| 132235 |
| 132236 |
| 66213 |
| 66214 |

**Predicted result**

| id |
| --- |
| 7909 |
| 7909 |
| 3963 |
| 3963 |

---

## 9. `bird_1136` (moderate, db `european_football_2`)

**Question.** How many players had the highest potential score for crossing that preferred to use their left foots while attacking?

**Evidence hint.** highest potential score for crossing refers to MAX(crossing); preferred to use their left foots while attacking refers to preferred_foot = 'left'

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT COUNT(t1.id) FROM Player_Attributes AS t1 WHERE t1.preferred_foot = 'left' AND t1.crossing = ( SELECT MAX(crossing) FROM Player_Attributes)
```

**Predicted SQL**

```sql
SELECT COUNT(DISTINCT `player_api_id`)
FROM `Player_Attributes`
WHERE `crossing` = (SELECT MAX(`crossing`) FROM `Player_Attributes`)
AND `preferred_foot` = 'left'
```

**Gold result**

| COUNT(t1.id) |
| --- |
| 3 |

**Predicted result**

| COUNT(DISTINCT "player_api_id") |
| --- |
| 2 |

---

## 10. `bird_1141` (moderate, db `european_football_2`)

**Question.** Does the KSV Cercle Brugge team have a slow, balanced or fast speed class?

**Evidence hint.** KSV Cercle Brugge refers to team_long_name = 'KSV Cercle Brugge'; speed class refers to buildUpPlaySpeedClass

_suggested: `aggregation`_

**Gold SQL**

```sql
SELECT DISTINCT t1.buildUpPlaySpeedClass FROM Team_Attributes AS t1 INNER JOIN Team AS t2 ON t1.team_api_id = t2.team_api_id WHERE t2.team_long_name = 'KSV Cercle Brugge'
```

**Predicted SQL**

```sql
SELECT TA.buildUpPlaySpeedClass
FROM Team T
JOIN Team_Attributes TA ON T.team_api_id = TA.team_api_id
WHERE T.team_long_name = 'KSV Cercle Brugge'
```

**Gold result**

| buildUpPlaySpeedClass |
| --- |
| Balanced |
| Fast |

**Predicted result**

| buildUpPlaySpeedClass |
| --- |
| Balanced |
| Balanced |
| Fast |
| Balanced |
| Balanced |
| Balanced |

---

## 11. `bird_1155` (simple, db `thrombosis_prediction`)

**Question.** List the patient ID, sex and birthday of patient with LDH beyond normal range.

**Evidence hint.** LDH beyond normal range refers to LDH > '500';

_suggested: `aggregation`_

**Gold SQL**

```sql
SELECT DISTINCT T1.ID, T1.SEX, T1.Birthday FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T2.LDH > 500
```

**Predicted SQL**

```sql
SELECT Patient.ID, Patient.SEX, Patient.Birthday
FROM Patient
JOIN Laboratory ON Patient.ID = Laboratory.ID
WHERE Laboratory.LDH > 500
```

**Gold result**

| ID | SEX | Birthday |
| --- | --- | --- |
| 27654 | F | 1936-03-25 |
| 57266 | M | 1923-07-25 |
| 81043 | M | 1967-03-25 |
| 540520 | F | 1941-11-21 |
| 912147 | F | 1975-02-17 |
| 944797 | F | 1975-01-31 |
| 1078741 | F | 1937-12-18 |
| 1110526 | F | 1966-01-01 |

_… 95 more rows_

**Predicted result**

| ID | SEX | Birthday |
| --- | --- | --- |
| 27654 | F | 1936-03-25 |
| 27654 | F | 1936-03-25 |
| 27654 | F | 1936-03-25 |
| 27654 | F | 1936-03-25 |
| 27654 | F | 1936-03-25 |
| 27654 | F | 1936-03-25 |
| 27654 | F | 1936-03-25 |
| 27654 | F | 1936-03-25 |

_… 1273 more rows_

---

## 12. `bird_116` (challenging, db `financial`)

**Question.** For the client whose loan was approved first in 1993/7/5, what is the increase rate of his/her account balance from 1993/3/22 to 1998/12/27?

**Evidence hint.** Increase rate of his/her account balance = [(balance of date A - balance of date B) / balance of Date B] * 100%

_suggested: `no_sql`_

**Gold SQL**

```sql
SELECT CAST((SUM(IIF(T3.date = '1998-12-27', T3.balance, 0)) - SUM(IIF(T3.date = '1993-03-22', T3.balance, 0))) AS REAL) * 100 / SUM(IIF(T3.date = '1993-03-22', T3.balance, 0)) FROM loan AS T1 INNER JOIN account AS T2 ON T1.account_id = T2.account_id INNER JOIN trans AS T3 ON T3.account_id = T2.account_id WHERE T1.date = '1993-07-05'
```

**Predicted SQL**

```sql
-- none produced
```

**Gold result**

| CAST((SUM(IIF(T3.date = '1998-12-27', T3.balance, 0)) - SUM(IIF(T3.date = '1993-03-22', T3.balance, 0))) AS REAL) * 100 / SUM(IIF(T3.date = '1993-03-22', T3.balance, 0)) |
| --- |
| 430.45454545454544 |

**Predicted result**

`no SQL`

---

## 13. `bird_1169` (challenging, db `thrombosis_prediction`)

**Question.** What is the ratio of male to female patients among all those with abnormal uric acid counts?

**Evidence hint.** male refers to SEX = 'M'; female refers to SEX = 'F'; abnormal uric acid refers to UA < = '8.0' where SEX = 'M', UA < = '6.5' where SEX = 'F'; calculation = DIVIDE(SUM(UA <= '8.0' and SEX = 'M'), SUM(UA <= '6.5 and SEX = 'F'))

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT CAST(SUM(CASE WHEN T2.UA <= 8.0 AND T1.SEX = 'M' THEN 1 ELSE 0 END) AS REAL) / SUM(CASE WHEN T2.UA <= 6.5 AND T1.SEX = 'F' THEN 1 ELSE 0 END) FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID
```

**Predicted SQL**

```sql
SELECT 
    (SELECT COUNT(DISTINCT p.ID) 
     FROM Patient p
     JOIN Laboratory l ON p.ID = l.ID
     WHERE p.SEX = 'M' AND l.UA <= 8.0) / 
    (SELECT COUNT(DISTINCT p.ID) 
     FROM Patient p
     JOIN Laboratory l ON p.ID = l.ID
     WHERE p.SEX = 'F' AND l.UA <= 6.5) AS ratio
```

**Gold result**

| CAST(SUM(CASE WHEN T2.UA <= 8.0 AND T1.SEX = 'M' THEN 1 ELSE 0 END) AS REAL) / SUM(CASE WHEN T2.UA <= 6.5 AND T1.SEX = 'F' THEN 1 ELSE 0 END) |
| --- |
| 0.20566810835419985 |

**Predicted result**

| ratio |
| --- |
| 0 |

---

## 14. `bird_1171` (challenging, db `thrombosis_prediction`)

**Question.** How many underage patients were examined during the course of the three-year period from 1990 to 1993?

**Evidence hint.** underage patients refers to year(Birthday) < 18; three-year period from 1990 to 1993 refers to year(`Examination Date`) between '1990' and '1993'

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT COUNT(T1.ID) FROM Patient AS T1 INNER JOIN Examination AS T2 ON T1.ID = T2.ID WHERE STRFTIME('%Y', T2.`Examination Date`) BETWEEN '1990' AND '1993' AND STRFTIME('%Y', T2.`Examination Date`) - STRFTIME('%Y', T1.Birthday) < 18
```

**Predicted SQL**

```sql
SELECT COUNT(DISTINCT Patient.ID)
FROM Patient
JOIN Examination ON Patient.ID = Examination.ID
WHERE strftime('%Y', Patient.Birthday) < 18
  AND strftime('%Y', Examination.`Examination Date`) BETWEEN 1990 AND 1993
```

**Gold result**

| COUNT(T1.ID) |
| --- |
| 1 |

**Predicted result**

| COUNT(DISTINCT Patient.ID) |
| --- |
| 0 |

---

## 15. `bird_118` (moderate, db `financial`)

**Question.** For loan amount less than USD100,000, what is the percentage of accounts that is still running with no issue.

**Evidence hint.** Status = 'C' stands for running contract, ok so far; Percentage of accounts by condition = [(total(amount) & condition) / (total amount)] * 100.

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT CAST(SUM(status = 'C') AS REAL) * 100 / COUNT(account_id) FROM loan WHERE amount < 100000
```

**Predicted SQL**

```sql
SELECT 
    (SUM(CASE WHEN loan.amount < 100000 AND loan.status = 'C' THEN loan.amount ELSE 0 END) * 100.0) / 
    SUM(CASE WHEN loan.amount < 100000 THEN loan.amount ELSE 0 END) AS percentage
FROM loan
```

**Gold result**

| CAST(SUM(status = 'C') AS REAL) * 100 / COUNT(account_id) |
| --- |
| 46.885245901639344 |

**Predicted result**

| percentage |
| --- |
| 47.55164983164983 |

---

## 16. `bird_1220` (simple, db `thrombosis_prediction`)

**Question.** Provide all ID, sex and birthday of patients whose urea nitrogen (UN) just within the borderline of passing?

**Evidence hint.** urea nitrogen (UN) just within the borderline of passing refers to UN = 29;

_suggested: `aggregation`_

**Gold SQL**

```sql
SELECT DISTINCT T1.ID, T1.SEX, T1.Birthday FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T2.UN = 29
```

**Predicted SQL**

```sql
SELECT p.ID, p.SEX, p.Birthday
FROM Patient p
JOIN Laboratory l ON p.ID = l.ID
WHERE l.UN = 29
```

**Gold result**

| ID | SEX | Birthday |
| --- | --- | --- |
| 27654 | F | 1936-03-25 |
| 444499 | M | 1954-01-24 |
| 912147 | F | 1975-02-17 |
| 1138737 | F | 1965-11-18 |
| 1185720 | F | 1953-10-12 |
| 2315140 | F | 1935-04-19 |
| 3171569 | F | 1971-03-19 |
| 3362815 | M | 1969-02-06 |

_… 12 more rows_

**Predicted result**

| ID | SEX | Birthday |
| --- | --- | --- |
| 27654 | F | 1936-03-25 |
| 27654 | F | 1936-03-25 |
| 444499 | M | 1954-01-24 |
| 444499 | M | 1954-01-24 |
| 444499 | M | 1954-01-24 |
| 912147 | F | 1975-02-17 |
| 912147 | F | 1975-02-17 |
| 1138737 | F | 1965-11-18 |

_… 36 more rows_

---

## 17. `bird_1238` (moderate, db `thrombosis_prediction`)

**Question.** Among the patients who were diagnosed with SLE, who is the oldest with normal hemoglobin level. Provide the ID and sex.

**Evidence hint.** diagnosed with SLE refers to Diagnosis = 'SLE'; The larger the birthday value, the younger the person is, and vice versa; normal hemoglobin level refers to 10 < HGB < 17;

_suggested: `no_sql`_

**Gold SQL**

```sql
SELECT T1.ID, T1.SEX FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T1.Diagnosis = 'SLE' AND T2.HGB > 10 AND T2.HGB < 17 ORDER BY T1.Birthday ASC LIMIT 1
```

**Predicted SQL**

```sql
-- none produced
```

**Gold result**

| ID | SEX |
| --- | --- |
| 4792723 | F |

**Predicted result**

`no SQL`

---

## 18. `bird_1239` (challenging, db `thrombosis_prediction`)

**Question.** Name the ID and age of patient with two or more laboratory examinations which show their hematoclit level exceeded the normal range.

**Evidence hint.** age = SUBTRACT(year(current_timestamp), year(Birthday)); patient with two or more laboratory examinations refers to COUNT(ID) > 2; hematoclit level exceeded the normal range refers to HCT > = 52;

_suggested: `no_sql`_

**Gold SQL**

```sql
SELECT DISTINCT T1.ID, STRFTIME('%Y', CURRENT_TIMESTAMP) - STRFTIME('%Y', T1.Birthday) FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T1.ID IN ( SELECT ID FROM Laboratory WHERE HCT >= 52 GROUP BY ID HAVING COUNT(ID) >= 2 )
```

**Predicted SQL**

```sql
-- none produced
```

**Gold result**

| ID | STRFTIME('%Y', CURRENT_TIMESTAMP) - STRFTIME('%Y', T1.Birthday) |
| --- | --- |
| 2307640 | 73 |
| 3182521 | 74 |
| 4007151 | 58 |

**Predicted result**

`no SQL`

---

## 19. `bird_1275` (moderate, db `thrombosis_prediction`)

**Question.** Among the patients who has a normal level of anti-centromere and a normal level of anti-SSB, how many of them are male?

**Evidence hint.** normal level of anti-centromere refers to CENTROMEA IN('-', '+-'); normal level of anti-SSB refers to SSB IN('-', '+-'); male refers to Sex = 'M'; Should consider DISTINCT in the final result;

_suggested: `exec_error`_

**Gold SQL**

```sql
SELECT COUNT(DISTINCT T1.ID) FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T2.CENTROMEA IN ('negative', '0') AND T2.SSB IN ('negative', '0') AND T1.SEX = 'M'
```

**Predicted SQL**

```sql
SELECT COUNT(DISTINCT p.ID)
FROM Patient p
JOIN Examination e ON p.ID = e.ID
JOIN Laboratory l ON p.ID = l.ID
WHERE e.`CENTROMEA` IN ('-', '+-')
  AND l.`SSB` IN ('-', '+-')
  AND p.SEX = 'M'
```

**Gold result**

| COUNT(DISTINCT T1.ID) |
| --- |
| 1 |

**Predicted result**

`OperationalError: no such column: e.CENTROMEA`

---

## 20. `bird_1350` (moderate, db `student_club`)

**Question.** What is the status of the event which bought "Post Cards, Posters" on 2019/8/20?

**Evidence hint.** 'Post Cards, Posters' is an expense description; on 2019/8/20 refers to expense_date = '2019-8-20'; status of event refers to event_status

_suggested: `value_format`_

**Gold SQL**

```sql
SELECT T1.event_status FROM budget AS T1 INNER JOIN expense AS T2 ON T1.budget_id = T2.link_to_budget WHERE T2.expense_description = 'Post Cards, Posters' AND T2.expense_date = '2019-08-20'
```

**Predicted SQL**

```sql
SELECT event.status
FROM expense
JOIN budget ON expense.link_to_budget = budget.budget_id
JOIN event ON budget.link_to_event = event.event_id
WHERE expense.expense_description = 'Post Cards, Posters'
AND expense.expense_date = '2019-8-20'
```

**Gold result**

| event_status |
| --- |
| Closed |

**Predicted result**

_(zero rows)_

---

## 21. `bird_1387` (moderate, db `student_club`)

**Question.** Which student has been entrusted to manage the budget for the Yearly Kickoff?

**Evidence hint.** name of students means the full name; full name refers to first_name, last_name;'Yearly Kickoff' is an event name;

_suggested: `exec_error`_

**Gold SQL**

```sql
SELECT T4.first_name, T4.last_name FROM event AS T1 INNER JOIN budget AS T2 ON T1.event_id = T2.link_to_event INNER JOIN expense AS T3 ON T2.budget_id = T3.link_to_budget INNER JOIN member AS T4 ON T3.link_to_member = T4.member_id WHERE T1.event_name = 'Yearly Kickoff'
```

**Predicted SQL**

```sql
SELECT member.first_name, member.last_name
FROM event
JOIN budget ON event.event_id = budget.link_to_event
JOIN member ON budget.link_to_member = member.member_id
WHERE event.event_name = 'Yearly Kickoff'
```

**Gold result**

| first_name | last_name |
| --- | --- |
| Sacha | Harrison |
| Elijah | Allen |
| Elijah | Allen |
| Sacha | Harrison |

**Predicted result**

`OperationalError: no such column: budget.link_to_member`

---

## 22. `bird_1390` (moderate, db `student_club`)

**Question.** Based on the total cost for all event, what is the percentage of cost for Yearly Kickoff event?

**Evidence hint.** percentage = DIVIDE(SUM(cost where event_name = 'Yearly Kickoff'), SUM(cost)) * 100

_suggested: `exec_error`_

**Gold SQL**

```sql
SELECT CAST(SUM(CASE WHEN T1.event_name = 'Yearly Kickoff' THEN T3.cost ELSE 0 END) AS REAL) * 100 / SUM(T3.cost) FROM event AS T1 INNER JOIN budget AS T2 ON T1.event_id = T2.link_to_event INNER JOIN expense AS T3 ON T2.budget_id = T3.link_to_budget
```

**Predicted SQL**

```sql
SELECT (SUM(CASE WHEN event.event_name = 'Yearly Kickoff' THEN expense.cost ELSE 0 END) * 100.0 / SUM(expense.cost)) AS percentage
FROM expense
JOIN event ON expense.link_to_event = event.event_id
```

**Gold result**

| CAST(SUM(CASE WHEN T1.event_name = 'Yearly Kickoff' THEN T3.cost ELSE 0 END) AS REAL) * 100 / SUM(T3.cost) |
| --- |
| 21.530164665276477 |

**Predicted result**

`OperationalError: no such column: expense.link_to_event`

---

## 23. `bird_1399` (moderate, db `student_club`)

**Question.** Did Maya Mclean attend the 'Women's Soccer' event?

**Evidence hint.** Maya Mclean is the full name; full name refers to first_name, last_name; 'Women's Soccer' is an event_name

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT CASE WHEN T3.event_name = 'Women''s Soccer' THEN 'YES' END AS result FROM member AS T1 INNER JOIN attendance AS T2 ON T1.member_id = T2.link_to_member INNER JOIN event AS T3 ON T2.link_to_event = T3.event_id WHERE T1.first_name = 'Maya' AND T1.last_name = 'Mclean'
```

**Predicted SQL**

```sql
SELECT EXISTS (
    SELECT 1
    FROM attendance
    JOIN event ON attendance.link_to_event = event.event_id
    JOIN member ON attendance.link_to_member = member.member_id
    WHERE event.event_name = 'Women''s Soccer'
        AND member.first_name = 'Maya'
        AND member.last_name = 'Mclean'
)
```

**Gold result**

| result |
| --- |
| YES |
| None |
| None |
| None |
| None |
| None |
| None |
| None |

_… 6 more rows_

**Predicted result**

| EXISTS(SELECT 1 FROM attendance JOIN event ON attendance.link_to_event = event.event_id JOIN member ON attendance.link_to_member = member.member_id WHERE event.event_name = 'Women''s Soccer' AND member.first_name = 'Maya' AND member.last_name = 'Mclean') |
| --- |
| 1 |

---

## 24. `bird_1427` (moderate, db `student_club`)

**Question.** What are the budget category of the events located at MU 215 and a guest speaker type with a 0 budget spent?

**Evidence hint.** budget category refers to category; events located at refers to location; type = 'Guest Speaker'; 0 budget spent refers to spent = 0;

_suggested: `aggregation`_

**Gold SQL**

```sql
SELECT DISTINCT T2.category, T1.type FROM event AS T1 INNER JOIN budget AS T2 ON T1.event_id = T2.link_to_event WHERE T1.location = 'MU 215' AND T2.spent = 0 AND T1.type = 'Guest Speaker'
```

**Predicted SQL**

```sql
SELECT budget.category
FROM budget
JOIN event ON budget.link_to_event = event.event_id
WHERE event.location = 'MU 215'
  AND event.type = 'Guest Speaker'
  AND budget.spent = 0
```

**Gold result**

| category | type |
| --- | --- |
| Speaker Gifts | Guest Speaker |
| Food | Guest Speaker |
| Parking | Guest Speaker |
| Advertisement | Guest Speaker |

**Predicted result**

| category |
| --- |
| Speaker Gifts |
| Food |
| Parking |
| Advertisement |
| Speaker Gifts |
| Speaker Gifts |
| Food |
| Food |

_… 8 more rows_

---

## 25. `bird_1435` (moderate, db `student_club`)

**Question.** List the names of closed event as "game" that was closed from 3/15/2019 to 3/20/2020.

**Evidence hint.** name of events refers event_name; game event that was closed refers to type = 'Game' where status = 'Closed'; event_date BETWEEN '2019-03-15' and '2020-03-20';

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT DISTINCT event_name FROM event WHERE type = 'Game' AND date(SUBSTR(event_date, 1, 10)) BETWEEN '2019-03-15' AND '2020-03-20' AND status = 'Closed'
```

**Predicted SQL**

```sql
SELECT event_name FROM event WHERE type = 'Game' AND status = 'Closed' AND event_date BETWEEN '2019-03-15' AND '2020-03-20'
```

**Gold result**

| event_name |
| --- |
| Women's Soccer |
| Football game |

**Predicted result**

| event_name |
| --- |
| Women's Soccer |
| Football game |
| Football game |

---

## 26. `bird_1481` (challenging, db `debit_card_specializing`)

**Question.** What is the difference in the annual average consumption of the customers with the least amount of consumption paid in CZK for 2013 between SME and LAM, LAM and KAM, and KAM and SME?

**Evidence hint.** annual average consumption of customer with the lowest consumption in each segment = total consumption per year / the number of customer with lowest consumption in each segment; Difference in annual average = SME's annual average - LAM's annual average; Difference in annual average = LAM's annual average - KAM's annual average; Year 2013 can be presented as Between 201301 And 201312; The first 4 strings of the Date values in the yearmonth table can represent year.

_suggested: `no_sql`_

**Gold SQL**

```sql
SELECT CAST(SUM(IIF(T1.Segment = 'SME', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) - CAST(SUM(IIF(T1.Segment = 'LAM', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) , CAST(SUM(IIF(T1.Segment = 'LAM', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) - CAST(SUM(IIF(T1.Segment = 'KAM', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) , CAST(SUM(IIF(T1.Segment = 'KAM', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) - CAST(SUM(IIF(T1.Segment = 'SME', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) FROM customers AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.Currency = 'CZK' AND T2.Consumption = ( SELECT MIN(Consumption) FROM yearmonth ) AND T2.Date BETWEEN 201301 AND 201312
```

**Predicted SQL**

```sql
-- none produced
```

**Gold result**

| CAST(SUM(IIF(T1.Segment = 'SME', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) - CAST(SUM(IIF(T1.Segment = 'LAM', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) | CAST(SUM(IIF(T1.Segment = 'LAM', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) - CAST(SUM(IIF(T1.Segment = 'KAM', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) | CAST(SUM(IIF(T1.Segment = 'KAM', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) - CAST(SUM(IIF(T1.Segment = 'SME', T2.Consumption, 0)) AS REAL) / COUNT(T1.CustomerID) |
| --- | --- | --- |
| 0.0 | 582092.86 | -582092.86 |

**Predicted result**

`no SQL`

---

## 27. `bird_1506` (moderate, db `debit_card_specializing`)

**Question.** Please list the product descriptions of the transactions taken place in the gas stations in the Czech Republic.

**Evidence hint.** Czech Republic can be represented as the Country value in the gasstations table is 'CZE';

_suggested: `exec_error`_

**Gold SQL**

```sql
SELECT DISTINCT T3.Description FROM transactions_1k AS T1 INNER JOIN gasstations AS T2 ON T1.GasStationID = T2.GasStationID INNER JOIN products AS T3 ON T1.ProductID = T3.ProductID WHERE T2.Country = 'CZE'
```

**Predicted SQL**

```sql
SELECT products.Description
FROM transactions_1k
JOIN gasstations ON transactions_1k.GasStationID = gasstations.GasStationID
WHERE gasstations.Country = 'CZE'
```

**Gold result**

| Description |
| --- |
| Nafta |
| Provoz.nápl. |
| Natural |
| Nemrz.kapal. |
| Oleje,tuky |
| Diesel + |
| LPG |
| Mytí vozidel |

_… 13 more rows_

**Predicted result**

`OperationalError: no such column: products.Description`

---

## 28. `bird_1526` (challenging, db `debit_card_specializing`)

**Question.** For the customer who paid 634.8 in 2012/8/25, what was the consumption decrease rate from Year 2012 to 2013?

**Evidence hint.** '2012/8/24' can be represented by '2012-08-24'; Consumption decrease rate = (consumption_2012 - consumption_2013) / consumption_2012

_suggested: `no_sql`_

**Gold SQL**

```sql
SELECT CAST(SUM(IIF(SUBSTRING(Date, 1, 4) = '2012', Consumption, 0)) - SUM(IIF(SUBSTRING(Date, 1, 4) = '2013', Consumption, 0)) AS FLOAT) / SUM(IIF(SUBSTRING(Date, 1, 4) = '2012', Consumption, 0)) FROM yearmonth WHERE CustomerID = ( SELECT T1.CustomerID FROM transactions_1k AS T1 INNER JOIN gasstations AS T2 ON T1.GasStationID = T2.GasStationID WHERE T1.Date = '2012-08-25' AND T1.Price  = 1513.12 )
```

**Predicted SQL**

```sql
-- none produced
```

**Gold result**

| CAST(SUM(IIF(SUBSTRING(Date, 1, 4) = '2012', Consumption, 0)) - SUM(IIF(SUBSTRING(Date, 1, 4) = '2013', Consumption, 0)) AS REAL) / SUM(IIF(SUBSTRING(Date, 1, 4) = '2012', Consumption, 0)) |
| --- |
| None |

**Predicted result**

`no SQL`

---

## 29. `bird_1528` (simple, db `debit_card_specializing`)

**Question.** What is the percentage of "premium" against the overall segment in Country = "SVK"?

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT CAST(SUM(IIF(Country = 'SVK' AND Segment = 'Premium', 1, 0)) AS FLOAT) * 100 / SUM(IIF(Country = 'SVK', 1, 0)) FROM gasstations
```

**Predicted SQL**

```sql
SELECT 
    (COUNT(CASE WHEN customers.Segment = 'premium' THEN 1 ELSE 0 END) * 100.0) / COUNT(DISTINCT customers.CustomerID) AS percentage
FROM 
    customers
JOIN 
    transactions_1k ON customers.CustomerID = transactions_1k.CustomerID
JOIN 
    gasstations ON transactions_1k.GasStationID = gasstations.GasStationID
WHERE 
    gasstations.Country = 'SVK'
```

**Gold result**

| CAST(SUM(IIF(Country = 'SVK' AND Segment = 'Premium', 1, 0)) AS REAL) * 100 / SUM(IIF(Country = 'SVK', 1, 0)) |
| --- |
| 35.68181818181818 |

**Predicted result**

| percentage |
| --- |
| 155.8139534883721 |

---

## 30. `bird_207` (challenging, db `toxicology`)

**Question.** What elements are in a double type bond?

**Evidence hint.** double type bond refers to bond_type = '=';

_suggested: `aggregation`_

**Gold SQL**

```sql
SELECT DISTINCT T1.element FROM atom AS T1 INNER JOIN bond AS T2 ON T1.molecule_id = T2.molecule_id INNER JOIN connected AS T3 ON T1.atom_id = T3.atom_id WHERE T2.bond_type = '='
```

**Predicted SQL**

```sql
SELECT 
  a1.element,
  a2.element
FROM 
  `connected` c
JOIN 
  `bond` b ON c.bond_id = b.bond_id
JOIN 
  `atom` a1 ON c.atom_id = a1.atom_id
JOIN 
  `atom` a2 ON c.atom_id2 = a2.atom_id
WHERE 
  b.bond_type = '='
```

**Gold result**

| element |
| --- |
| c |
| o |
| cl |
| h |
| s |
| n |
| p |
| f |

_… 5 more rows_

**Predicted result**

| element | element |
| --- | --- |
| c | o |
| o | c |
| c | c |
| c | c |
| o | c |
| c | o |
| c | o |
| o | c |

_… 2808 more rows_

---

## 31. `bird_244` (moderate, db `toxicology`)

**Question.** Is the molecule with the most double bonds carcinogenic?

**Evidence hint.** double bond refers to bond_type = ' = '; label = '+' mean molecules are carcinogenic

_suggested: `value_format`_

**Gold SQL**

```sql
SELECT T1.label FROM molecule AS T1 INNER JOIN ( SELECT T.molecule_id, COUNT(T.bond_type) FROM bond AS T WHERE T.bond_type = '=' GROUP BY T.molecule_id ORDER BY COUNT(T.bond_type) DESC LIMIT 1 ) AS T2 ON T1.molecule_id = T2.molecule_id
```

**Predicted SQL**

```sql
SELECT CASE WHEN m.label = '+' THEN 'yes' ELSE 'no' END
FROM molecule m
JOIN (
    SELECT molecule_id, COUNT(*) AS double_count
    FROM bond
    WHERE bond_type = ' = '
    GROUP BY molecule_id
) b ON m.molecule_id = b.molecule_id
ORDER BY double_count DESC
LIMIT 1
```

**Gold result**

| label |
| --- |
| + |

**Predicted result**

_(zero rows)_

---

## 32. `bird_341` (simple, db `card_games`)

**Question.** What are the borderless cards available without powerful foils?

**Evidence hint.** borderless' refers to borderColor; poweful foils refers to cardKingdomFoilId paired with cardKingdomId AND cardKingdomId is not null

_suggested: `value_format`_

**Gold SQL**

```sql
SELECT id FROM cards WHERE borderColor = 'borderless' AND (cardKingdomId IS NULL OR cardKingdomId IS NULL)
```

**Predicted SQL**

```sql
SELECT * FROM cards WHERE borderColor IS NULL AND (cardKingdomFoilId IS NULL OR cardKingdomId IS NULL) AND availability IS NOT NULL
```

**Gold result**

| id |
| --- |
| 23004 |
| 25350 |
| 25355 |
| 25357 |
| 25359 |
| 25361 |
| 33094 |
| 33095 |

_… 59 more rows_

**Predicted result**

_(zero rows)_

---

## 33. `bird_344` (moderate, db `card_games`)

**Question.** List all the mythic rarity print cards banned in gladiator format.

**Evidence hint.** mythic rarity printing refers to rarity = 'mythic'; card banned refers to status = 'Banned'; in gladiator format refers to format = 'gladiator';

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT DISTINCT T1.id FROM cards AS T1 INNER JOIN legalities AS T2 ON T1.uuid = T2.uuid WHERE T2.format = 'gladiator' AND T2.status = 'Banned' AND T1.rarity = 'mythic'
```

**Predicted SQL**

```sql
SELECT cards.name
FROM cards
JOIN legalities ON cards.uuid = legalities.uuid
WHERE cards.rarity = 'mythic'
  AND legalities.format = 'gladiator'
  AND legalities.status = 'Banned'
```

**Gold result**

| id |
| --- |
| 17983 |
| 18058 |
| 29523 |
| 38736 |
| 38737 |

**Predicted result**

| name |
| --- |
| Oko, Thief of Crowns |
| Oko, Thief of Crowns |
| Nexus of Fate |
| Oko, Thief of Crowns |
| Oko, Thief of Crowns |

---

## 34. `bird_408` (moderate, db `card_games`)

**Question.** How many unknown power cards contain info about the triggered ability

**Evidence hint.** unknown power cards refers to power is null or power = '*';contain info about the triggered ability refers to text contains 'triggered ability'

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT Count(DISTINCT T1.id) FROM cards AS T1 INNER JOIN rulings AS T2 ON T1.uuid = T2.uuid WHERE (T1.power IS NULL OR T1.power = '*') AND T2.text LIKE '%triggered ability%'
```

**Predicted SQL**

```sql
SELECT COUNT(*) FROM cards WHERE (power IS NULL OR power = '*') AND text LIKE '%triggered ability%'
```

**Gold result**

| COUNT(DISTINCT T1.id) |
| --- |
| 1382 |

**Predicted result**

| COUNT(*) |
| --- |
| 35 |

---

## 35. `bird_50` (simple, db `california_schools`)

**Question.** What is the postal street address for the school with the 7th highest Math average? Indicate the school's name.

**Evidence hint.** Postal street and mailing street are synonyms.

_suggested: `metric_artifact`_

**Gold SQL**

```sql
SELECT T2.MailStreet, T2.School FROM satscores AS T1 INNER JOIN schools AS T2 ON T1.cds = T2.CDSCode ORDER BY T1.AvgScrMath DESC LIMIT 6, 1
```

**Predicted SQL**

```sql
SELECT schools.School, schools.MailStreet
FROM schools
JOIN satscores ON schools.CDSCode = satscores.cds
ORDER BY satscores.AvgScrMath DESC
LIMIT 1 OFFSET 6
```

**Gold result**

| MailStreet | School |
| --- | --- |
| 25 Churchill Avenue | None |

**Predicted result**

| School | MailStreet |
| --- | --- |
| None | 25 Churchill Avenue |

---

## 36. `bird_518` (moderate, db `card_games`)

**Question.** Which of the play format has the highest number of banned status? Indicate the play format and the names of all the card meet the condition.

**Evidence hint.** play format refers to format; banned status refers to status = 'Banned'; the highest number of banned status refers to MAX(COUNT(status = 'Banned'))

_suggested: `gold_wrong`_

**Gold SQL**

```sql
WITH MaxBanned AS (SELECT format, COUNT(*) AS count_banned FROM legalities WHERE status = 'Banned' GROUP BY format ORDER BY COUNT(*) DESC LIMIT 1) SELECT T2.format, T1.name FROM cards AS T1 INNER JOIN legalities AS T2 ON T2.uuid = T1.uuid INNER JOIN MaxBanned MB ON MB.format = T2.format WHERE T2.status = 'Banned'
```

**Predicted SQL**

```sql
SELECT l.format, c.name
FROM legalities l
JOIN cards c ON l.uuid = c.uuid
WHERE l.status = 'Banned'
AND l.format = (
    SELECT l2.format
    FROM legalities l2
    WHERE l2.status = 'Banned'
    GROUP BY l2.format
    ORDER BY COUNT(l2.uuid) DESC
    LIMIT 1
)
```

**Gold result**

`OperationalError: interrupted`

**Predicted result**

| format | name |
| --- | --- |
| legacy | Balance |
| legacy | Crusade |
| legacy | Ancestral Recall |
| legacy | Time Walk |
| legacy | Timetwister |
| legacy | Contract from Below |
| legacy | Darkpact |
| legacy | Demonic Attorney |

_… 538 more rows_

---

## 37. `bird_595` (moderate, db `codebase_community`)

**Question.** Which user have only one post history per post and having at least 1000 views?

**Evidence hint.** having at least 1000 view refers to Views > = 1000; user refers to UserId

_suggested: `exec_error`_

**Gold SQL**

```sql
SELECT T2.UserId FROM users AS T1 INNER JOIN postHistory AS T2 ON T1.Id = T2.UserId INNER JOIN posts AS T3 ON T2.PostId = T3.Id WHERE T3.ViewCount >= 1000 GROUP BY T2.UserId HAVING COUNT(DISTINCT T2.PostHistoryTypeId) = 1
```

**Predicted SQL**

```sql
SELECT users.Id
FROM users
JOIN posts ON users.Id = posts.OwnerUserId
WHERE posts.ViewCount >= 1000
  AND (SELECT COUNT(*) FROM postHistory WHERE postHistory.PostId = posts.Id) = 1
```

**Gold result**

| UserId |
| --- |
| 57 |
| 61 |
| 80 |
| 124 |
| 139 |
| 334 |
| 486 |
| 495 |

_… 86 more rows_

**Predicted result**

`OperationalError: interrupted`

---

## 38. `bird_604` (moderate, db `codebase_community`)

**Question.** What is the average of the up votes and the average user age for users creating more than 10 posts?

**Evidence hint.** creating more than 10 post refers to Count (UserId) > 10; average of the up votes = Divide (Sum(UpVotes), Count (UserId)); average age = Divide (Sum(Age), Count(UserId))

_suggested: `aggregation`_

**Gold SQL**

```sql
SELECT AVG(T1.UpVotes), AVG(T1.Age) FROM users AS T1 INNER JOIN ( SELECT OwnerUserId, COUNT(*) AS post_count FROM posts GROUP BY OwnerUserId HAVING post_count > 10) AS T2 ON T1.Id = T2.OwnerUserId
```

**Predicted SQL**

```sql
SELECT AVG(users.UpVotes), AVG(users.Age)
FROM users
JOIN posts ON users.Id = posts.OwnerUserId
GROUP BY users.Id
HAVING COUNT(posts.Id) > 10
```

**Gold result**

| AVG(T1.UpVotes) | AVG(T1.Age) |
| --- | --- |
| 182.2832618025751 | 34.083333333333336 |

**Predicted result**

| AVG(users.UpVotes) | AVG(users.Age) |
| --- | --- |
| 5007.0 | None |
| 662.0 | 35.0 |
| 47.0 | 41.0 |
| 604.0 | 36.0 |
| 693.0 | None |
| 328.0 | 62.0 |
| 138.0 | None |
| 40.0 | None |

_… 1157 more rows_

---

## 39. `bird_62` (challenging, db `california_schools`)

**Question.** What is the total number of non-chartered schools in the county of Los Angeles with a percent (%) of eligible free meals for grades 1 through 12 that is less than 0.18%?

**Evidence hint.** non-chartered schools refer to schools whose Charter = 0; K-12 means grades 1 through 12; percent of eligible free rate for K-12 = `Free Meal Count (K-12)` * 100 / `Enrollment (K-12)`

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT COUNT(T2.School) FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.County = 'Los Angeles' AND T2.Charter = 0 AND CAST(T1.`Free Meal Count (K-12)` AS REAL) * 100 / T1.`Enrollment (K-12)` < 0.18
```

**Predicted SQL**

```sql
SELECT COUNT(*)
FROM frpm
JOIN schools ON frpm.CDSCode = schools.CDSCode
WHERE schools.County = 'Los Angeles'
  AND schools.Charter = 0
  AND frpm.`Percent (%) Eligible Free (K-12)` < 0.18
```

**Gold result**

| COUNT(T2.School) |
| --- |
| 1 |

**Predicted result**

| COUNT(*) |
| --- |
| 201 |

---

## 40. `bird_629` (simple, db `codebase_community`)

**Question.** Calculate the ratio of votes in 2010 and 2011.

**Evidence hint.** DIVIDE(COUNT(Id where YEAR(CreationDate) = 2010), COUNT(Id where YEAR(CreationDate) = 2011)) FROM votes;

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT CAST(SUM(IIF(STRFTIME('%Y', CreationDate) = '2010', 1, 0)) AS REAL) / SUM(IIF(STRFTIME('%Y', CreationDate) = '2011', 1, 0)) FROM votes
```

**Predicted SQL**

```sql
SELECT 
    SUM(CASE WHEN strftime('%Y', CreationDate) = '2010' THEN 1 ELSE 0 END) 
    / 
    SUM(CASE WHEN strftime('%Y', CreationDate) = '2011' THEN 1 ELSE 0 END)
FROM votes
```

**Gold result**

| CAST(SUM(IIF(STRFTIME('%Y', CreationDate) = '2010', 1, 0)) AS REAL) / SUM(IIF(STRFTIME('%Y', CreationDate) = '2011', 1, 0)) |
| --- |
| 1.307099679981036 |

**Predicted result**

| SUM(CASE WHEN STRFTIME('%Y', CreationDate) = '2010' THEN 1 ELSE 0 END) / SUM(CASE WHEN STRFTIME('%Y', CreationDate) = '2011' THEN 1 ELSE 0 END) |
| --- |
| 1 |

---

## 41. `bird_685` (moderate, db `codebase_community`)

**Question.** Identify the total views on the post 'Computer Game Datasets'. Name the user who posted it last time.

**Evidence hint.** total views refer to ViewCount; Name the user refers to DisplayName; post 'Computer Game Datasets' refers to Text = 'Computer Game Datasets';

_suggested: `value_format`_

**Gold SQL**

```sql
SELECT T2.ViewCount, T3.DisplayName FROM postHistory AS T1 INNER JOIN posts AS T2 ON T1.PostId = T2.Id INNER JOIN users AS T3 ON T2.LastEditorUserId = T3.Id WHERE T1.Text = 'Computer Game Datasets'
```

**Predicted SQL**

```sql
SELECT posts.ViewCount, posts.LastEditorDisplayName
FROM posts
WHERE posts.Title = 'Computer Game Datasets'
```

**Gold result**

| ViewCount | DisplayName |
| --- | --- |
| 1708 | mbq |

**Predicted result**

_(zero rows)_

---

## 42. `bird_710` (simple, db `codebase_community`)

**Question.** In posts with 1 comment, how many of the comments have 0 score?

**Evidence hint.** in posts with 1 comment refers to CommentCount = 1;

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT COUNT(T1.id) FROM comments AS T1 INNER JOIN posts AS T2 ON T1.PostId = T2.Id WHERE T2.CommentCount = 1 AND T2.Score = 0
```

**Predicted SQL**

```sql
SELECT COUNT(comments.Id)
FROM comments
JOIN posts ON comments.PostId = posts.Id
WHERE posts.CommentCount = 1 AND comments.Score = 0
```

**Gold result**

| COUNT(T1.id) |
| --- |
| 2888 |

**Predicted result**

| COUNT(comments.Id) |
| --- |
| 10997 |

---

## 43. `bird_79` (moderate, db `california_schools`)

**Question.** Between San Diego and Santa Barbara, which county offers the most number of schools that does not offer physical building? Indicate the amount.

**Evidence hint.** 'Does not offer physical building' means Virtual = F in the database.

_suggested: `metric_artifact`_

**Gold SQL**

```sql
SELECT County, COUNT(Virtual) FROM schools WHERE (County = 'San Diego' OR County = 'Santa Barbara') AND Virtual = 'F' GROUP BY County ORDER BY COUNT(Virtual) DESC LIMIT 1
```

**Predicted SQL**

```sql
SELECT COUNT(*) 
FROM schools 
WHERE County IN ('San Diego', 'Santa Barbara') 
  AND Virtual = 'F'
ORDER BY COUNT(*) DESC 
LIMIT 1
```

**Gold result**

| County | COUNT(Virtual) |
| --- | --- |
| San Diego | 8 |

**Predicted result**

| COUNT(*) |
| --- |
| 9 |

---

## 44. `bird_892` (moderate, db `formula_1`)

**Question.** State the driver with the most points scored. Find his full name with that points.

**Evidence hint.** the most points scored refers to max(points); Full name of the driver refers to drivers.forename and drivers.surname;

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT T3.forename, T3.surname, T2.points FROM races AS T1 INNER JOIN driverStandings AS T2 ON T2.raceId = T1.raceId INNER JOIN drivers AS T3 ON T3.driverId = T2.driverId ORDER BY T2.points DESC LIMIT 1
```

**Predicted SQL**

```sql
SELECT drivers.forename, drivers.surname, results.points
FROM drivers
JOIN results ON drivers.driverId = results.driverId
ORDER BY results.points DESC
LIMIT 1
```

**Gold result**

| forename | surname | points |
| --- | --- | --- |
| Sebastian | Vettel | 397.0 |

**Predicted result**

| forename | surname | points |
| --- | --- | --- |
| Lewis | Hamilton | 50.0 |

---

## 45. `bird_896` (challenging, db `formula_1`)

**Question.** Calculate the percentage whereby Hamilton was not at the 1st track of the the f1 circuit since 2010.

**Evidence hint.** percentage = DIVIDE(COUNT(raceId) where surname = 'Hamilton' and position>1), (COUNT(raceId) where surname = 'Hamilton'); since 2010 refers to year >= 2010

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT CAST(COUNT(CASE WHEN T2.position <> 1 THEN T2.position END) AS REAL) * 100 / COUNT(T2.driverStandingsId) FROM races AS T1 INNER JOIN driverStandings AS T2 ON T2.raceId = T1.raceId INNER JOIN drivers AS T3 ON T3.driverId = T2.driverId WHERE T3.surname = 'Hamilton' AND T1.year >= 2010
```

**Predicted SQL**

```sql
SELECT 
    (COUNT(CASE WHEN results.position > 1 THEN 1 ELSE NULL END) * 100.0) / COUNT(results.driverId) AS percentage
FROM 
    results
JOIN 
    drivers ON results.driverId = drivers.driverId
JOIN 
    races ON results.raceId = races.raceId
WHERE 
    drivers.surname = 'Hamilton'
    AND races.year >= 2010
```

**Gold result**

| CAST(COUNT(CASE WHEN T2.position <> 1 THEN T2.position END) AS REAL) * 100 / COUNT(T2.driverStandingsId) |
| --- |
| 73.2394366197183 |

**Predicted result**

| percentage |
| --- |
| 54.225352112676056 |

---

## 46. `bird_906` (moderate, db `formula_1`)

**Question.** Which was Lewis Hamilton first race? What was his points recorded for his first race event?

**Evidence hint.** first race refers to min(Year); Lewis Hamiltonrefers to the full name of the driver; Full name of the driver refers to drivers.forename and drivers.surname;

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT T1.name, T2.points FROM races AS T1 INNER JOIN driverStandings AS T2 ON T2.raceId = T1.raceId INNER JOIN drivers AS T3 ON T3.driverId = T2.driverId WHERE T3.forename = 'Lewis' AND T3.surname = 'Hamilton' ORDER BY T1.year ASC LIMIT 1
```

**Predicted SQL**

```sql
SELECT races.name, results.points
FROM races
JOIN results ON races.raceId = results.raceId
JOIN drivers ON results.driverId = drivers.driverId
WHERE drivers.forename = 'Lewis' AND drivers.surname = 'Hamilton'
ORDER BY races.year ASC
LIMIT 1
```

**Gold result**

| name | points |
| --- | --- |
| Malaysian Grand Prix | 14.0 |

**Predicted result**

| name | points |
| --- | --- |
| Malaysian Grand Prix | 8.0 |

---

## 47. `bird_915` (simple, db `formula_1`)

**Question.** Which country is the oldest driver from?

**Evidence hint.** date of birth refers to drivers.dob; The larger the birthday value, the younger the person is, and vice versa;

_suggested: `schema_linking`_

**Gold SQL**

```sql
SELECT nationality FROM drivers WHERE dob IS NOT NULL ORDER BY dob ASC LIMIT 1
```

**Predicted SQL**

```sql
SELECT nationality FROM drivers ORDER BY dob ASC LIMIT 1
```

**Gold result**

| nationality |
| --- |
| French |

**Predicted result**

| nationality |
| --- |
| South African |

---

## 48. `bird_967` (simple, db `formula_1`)

**Question.** State code numbers of top 3 yougest drivers. How many Netherlandic drivers among them?

**Evidence hint.** youngest driver refers to Max (year(dob)); Netherlandic and Dutch refer to the same country

_suggested: `no_sql`_

**Gold SQL**

```sql
SELECT COUNT(*) FROM ( SELECT T1.nationality FROM drivers AS T1 ORDER BY JULIANDAY(T1.dob) DESC LIMIT 3) AS T3 WHERE T3.nationality = 'Dutch'
```

**Predicted SQL**

```sql
-- none produced
```

**Gold result**

| COUNT(*) |
| --- |
| 1 |

**Predicted result**

`no SQL`

---

## 49. `bird_989` (moderate, db `formula_1`)

**Question.** Who is the champion of the Canadian Grand Prix in 2008? Indicate his finish time.

**Evidence hint.** Only the time of the champion shows in the format of "hour: minutes: seconds.millionsecond";

_suggested: `metric_artifact`_

**Gold SQL**

```sql
SELECT T1.time FROM results AS T1 INNER JOIN races AS T2 ON T1.raceId = T2.raceId WHERE T2.name = 'Canadian Grand Prix' AND T2.year = 2008 AND T1.time LIKE '_:%:__.___'
```

**Predicted SQL**

```sql
SELECT drivers.surname, results.time
FROM results
JOIN drivers ON results.driverId = drivers.driverId
JOIN races ON results.raceId = races.raceId
WHERE races.year = 2008
AND races.name = 'Canadian Grand Prix'
AND results.position = 1
```

**Gold result**

| time |
| --- |
| 1:36:24.227 |

**Predicted result**

| surname | time |
| --- | --- |
| Kubica | 1:36:24.227 |

---

## 50. `bird_994` (challenging, db `formula_1`)

**Question.** Which constructor scored most points from Monaco Grand Prix between 1980 and 2010? List the score, name and nationality of this team.

**Evidence hint.** Monaco Grand Priz refers to the race; race in year between 1980 and 2010

_suggested: `metric_artifact`_

**Gold SQL**

```sql
SELECT SUM(T1.points), T2.name, T2.nationality FROM constructorResults AS T1 INNER JOIN constructors AS T2 ON T1.constructorId = T2.constructorId INNER JOIN races AS T3 ON T3.raceid = T1.raceid WHERE T3.name = 'Monaco Grand Prix' AND T3.year BETWEEN 1980 AND 2010 GROUP BY T2.name ORDER BY SUM(T1.points) DESC LIMIT 1
```

**Predicted SQL**

```sql
SELECT 
    c.name,
    c.nationality,
    SUM(res.points) AS total_points
FROM 
    races r
JOIN 
    results res ON r.raceId = res.raceId
JOIN 
    constructors c ON res.constructorId = c.constructorId
WHERE 
    r.name = 'Monaco Grand Prix'
    AND r.year BETWEEN 1980 AND 2010
GROUP BY 
    c.constructorId
ORDER BY 
    total_points DESC
LIMIT 1
```

**Gold result**

| SUM(T1.points) | name | nationality |
| --- | --- | --- |
| 218.5 | McLaren | British |

**Predicted result**

| name | nationality | total_points |
| --- | --- | --- |
| McLaren | British | 218.5 |

---
