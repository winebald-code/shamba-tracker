"""
Checks that uploads survive on object storage.

Map snapshots, generated reports and homepage images used to be written to the
container's own disk, which most hosts recreate on every deploy. These run the
whole upload-render-serve path against a mock S3 API, so a regression that
quietly writes to local disk again is caught here.

Needs boto3 and moto:  pip install boto3 "moto[s3]"

    python tests/test_storage.py
"""
import os, sys, tempfile, io, shutil
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT); os.chdir(ROOT)
from moto import mock_aws
import boto3
P=F=0
def chk(l,c,d=""):
    global P,F
    print(("  [PASS] " if c else "  [FAIL] ")+l+("" if c else "  -> "+str(d))); P,F=(P+1,F) if c else (P,F+1)

mock = mock_aws(); mock.start()
boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="shamba-test")

TMP=tempfile.mkdtemp()
os.environ.update(DATABASE_URL="sqlite:///"+os.path.join(TMP,"s3.db"), SECRET_KEY="t",
                  ADMIN_EMAIL="a@a.com", ADMIN_PASSWORD="password123",
                  S3_BUCKET="shamba-test", S3_REGION="us-east-1",
                  S3_ACCESS_KEY_ID="test", S3_SECRET_ACCESS_KEY="test")
import app as appmod
from models import db, Farm, Flight, Finding
from datetime import date

chk("S3 backend selected", appmod.STORE.name == "s3", appmod.STORE.name)

with appmod.app.app_context():
    fm=Farm(name="S3 Farm", crop="Maize", acreage=10.0); db.session.add(fm); db.session.commit()
    fl=Flight(farm_id=fm.id, season="2026LR", flight_number=1, flights_planned=2,
              crop="Maize", acreage=10.0, flight_date=date(2026,3,1)); db.session.add(fl); db.session.commit()
    db.session.add(Finding(flight_id=fl.id, category="Pest / Disease", colour_meaning="Needs testing",
        colour_swatch="#D64550", observation="o", likely_cause="c", recommendation="r", area_acres=2.0))
    db.session.commit(); FLID=fl.id; TOK=fl.share_token

c=appmod.app.test_client()
c.post("/login",data={"email":"a@a.com","password":"password123"},follow_redirects=True)

print("\n=== 1. map upload goes to the bucket, not the disk ===")
img=open("samples/sample_annotated_map.jpg","rb").read()
r=c.post(f"/flights/{FLID}/upload_map", data={"map":(io.BytesIO(img),"map.jpg")},
         content_type="multipart/form-data", follow_redirects=True)
chk("upload accepted", r.status_code==200, r.status_code)
with appmod.app.app_context():
    name=db.session.get(Flight,FLID).map_image
chk("map_image recorded", bool(name), name)
keys=[o["Key"] for o in boto3.client("s3",region_name="us-east-1").list_objects_v2(Bucket="shamba-test").get("Contents",[])]
chk("object is in the bucket", any(name in k for k in keys), keys)
chk("nothing written to local uploads/", not os.path.exists(os.path.join(appmod.UPLOAD_DIR, name)))

print("\n=== 2. it is served back correctly ===")
r=c.get(f"/uploads/{name}")
chk("served with 200", r.status_code==200, r.status_code)
chk("bytes match what was uploaded", r.data==img, f"{len(r.data)} vs {len(img)}")
chk("content type is an image", r.headers["Content-Type"].startswith("image/"), r.headers.get("Content-Type"))
chk("a missing object 404s", c.get("/uploads/nope.jpg").status_code==404)

print("\n=== 3. the report renders with the map from the bucket ===")
r=c.post(f"/flights/{FLID}/generate", follow_redirects=True)
chk("generate succeeds", r.status_code==200, r.status_code)
pdf=c.get(f"/flights/{FLID}/report.pdf")
chk("a real PDF comes back", pdf.data[:5]==b"%PDF-", pdf.data[:12])
chk("the PDF is big enough to contain the map", len(pdf.data)>60000, len(pdf.data))
with appmod.app.app_context():
    rp=db.session.get(Flight,FLID).report_pdf
keys=[o["Key"] for o in boto3.client("s3",region_name="us-east-1").list_objects_v2(Bucket="shamba-test").get("Contents",[])]
chk("the generated PDF is in the bucket", any(rp in k for k in keys), (rp, keys))
chk("no temp file left behind", not any(f.startswith("tmp") for f in os.listdir(tempfile.gettempdir())
     if f.endswith(".jpg")), "leftover temp map")

print("\n=== 4. the farmer's public report works ===")
pub=appmod.app.test_client()
chk("public page opens", pub.get(f"/r/{TOK}").status_code==200)
chk("public PDF downloads", pub.get(f"/r/{TOK}/report.pdf").data[:5]==b"%PDF-")

print("\n=== 5. homepage image upload survives too ===")
png=(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
     b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
r=c.post("/homepage/image/hero_image", data={"image":(io.BytesIO(png),"h.png")},
         content_type="multipart/form-data", follow_redirects=True)
chk("upload accepted", r.status_code==200)
from models import SiteContent
with appmod.app.app_context():
    row=SiteContent.query.filter_by(key="hero_image").first()
chk("stored as an uploads/ reference", row and row.value.startswith("uploads/"), row.value if row else None)
keys=[o["Key"] for o in boto3.client("s3",region_name="us-east-1").list_objects_v2(Bucket="shamba-test").get("Contents",[])]
chk("the image is in the bucket", any("home-hero-image" in k for k in keys), keys)
home=appmod.app.test_client().get("/?preview=1").data.decode()
chk("homepage points at the uploads route", "/uploads/home-hero-image.png" in home)
chk("shipped images still come from static/", "/static/img/acre-logo.png" in home)
print(f"\n  {P} passed, {F} failed")
mock.stop()
sys.exit(1 if F else 0)
