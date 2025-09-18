# Upload
SID=$(curl -s -F "file=@/path/to/lexsy.docx" http://localhost:8000/upload | python3 -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')

# Fill currency variants (all should come back normalized to dollar format)
for v in '$10,000,000' '10m' '1.25m' '250000' '250k' 'USD 5,500,000'; do
  curl -s -X POST http://localhost:8000/fill \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$SID\",\"key\":\"post_money_valuation_cap\",\"value\":\"$v\"}" \
  | python3 -m json.tool | sed -n '1,18p'
done

# Fill date (various inputs -> Month DD, YYYY)
for d in '2025-09-15' '9/15/2025' '15 Sep 2025' 'September 15 2025' '15th September 2025'; do
  curl -s -X POST http://localhost:8000/fill \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"$SID\",\"key\":\"date_of_safe\",\"value\":\"$d\"}" \
  | python3 -m json.tool | sed -n '1,18p'
done

# Download and verify headers/footers replaced too (if your template uses them)
curl -OJ "http://localhost:8000/download?session_id=$SID"

