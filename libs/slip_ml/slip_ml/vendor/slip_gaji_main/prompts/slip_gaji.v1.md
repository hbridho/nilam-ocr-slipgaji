You read Indonesian payslips (slip gaji). The text below was produced by OCR from one page, so it may contain reading errors and the columns are separated by two spaces.

Extract these {n} fields:
{fields}

Rules:
- Reply with ONE flat JSON object using exactly the field names above as keys. No other keys.
- Include every field. Use null for any field that is not printed on this document.
- NEVER guess or infer a value. If you cannot see it, it is null.
- Money fields: digits only. No "Rp", no thousand separators, no decimals. Example: 4000000
- Do not confuse a total with a component. "TOTAL PENDAPATAN" / "GAJI BRUTO" is the total, not "tunjangan_transport" or any single allowance.
- "periode": format YYYY-MM. Example: 2025-08
- Copy identity values exactly as printed, including their capitalisation.
- OCR mangles characters — read past it: "Gajl Pokok" is the label "Gaji Pokok", "0KT0BER" is Oktober, "l505" is "1505", "NlK" is "NIK". Those are labels PRINTED on the page; the JSON keys you reply with are always the snake_case names listed above.

Reading the layout:
- TWO OR MORE SPACES MEAN A NEW COLUMN. A value ends at that gap. In "Jabatan  :Pengawas Lapangan  Alpa  :0" the job title is "Pengawas Lapangan" — "Alpa  :0" is a different column with its own label. Never weld two columns into one value.
- A label with no field of its own still ends the value before it: Kehadiran, Alpa, Terlambat, Tax ID, PTKP, NPWP, Group/Grade, Bank Acc, Nomor, Tanggal, Absen, Shift.
- A four-digit number that is a YEAR is never a money amount. "Total Biaya Kesehatan Tahun 2023" has no amount on it.

Where these fields hide:
- "nomor_induk_karyawan" is any employee identifier, whatever the sheet calls it: NIP, NIK, NRP, No. Induk Karyawan, No. Karyawan, ID No, ID.NO, Pers. ID, Employee ID, Badge No, Emp. Code. It is NOT the tax number (NPWP / Tax ID) and NOT the bank account.
- When an ID and a name share one cell — "ID / Name  2058 / Bobby Septian", "ID/Name  :0831 - I GUSTI NGURAH SAPUTRA" — split them: the digits are "nomor_induk_karyawan", the rest is "nama_karyawan".
- "tunjangan_lain" is the catch-all for any ALLOWANCE (income, above the deductions block) that has no field of its own: Upah Persenan, Jasa Penunjang, Jasa Kebersamaan, Tunj. Orientasi, Allowance or Bonus, Fee Penjualan, Tax Allowance, Tunj. Apresiasi, Tunj. Kerajinan. Return THAT ROW'S OWN AMOUNT, copied as printed. Never add several rows together, and never put a total or a subtotal here. It is never a deduction.
- "divisi" is the organisational unit: Divisi, Departemen, Department, Bagian, Unit Kerja, Org. Unit, Seksi, Area. "jabatan" is the job title (Posisi, Position, Occupation, Jabatan).
- "nama_perusahaan" is the body that EMPLOYS this person. When a parent government and a specific office are both printed — "PEMERINTAH KABUPATEN CIANJUR" above "RUMAH SAKIT UMUM DAERAH SAYANG", "PEMERINTAH KABUPATEN TABANAN" above "BADAN KEPEGAWAIAN DAN PENGEMBANGAN SDM" — answer with the SPECIFIC office, not the parent.
- "nama_perusahaan" is never a column heading (Amount, Jumlah, Total, Income, Deduction), never a bank account row ("Bank Acc :0242948696"), and never the payroll bank the salary is transferred to. A slip that says "Polres Banjarnegara" at the top and "Bank Jateng" beside an account number was issued by the Polres.
- When the sheet prints ONE amount only, that amount is both "total_pendapatan" and "gaji_bersih"; if it is labelled as the basic salary, it is "gaji_pokok" as well.

Seven worked examples, different layouts each time. The field list in a real request may be shorter — answer only for the fields asked, but read values the same way.

OCR TEXT:
PT SUMBER REJEKI MAKMUR
Slip Gaji Bulan Agustus 2025
Nama : Andi Saputra    Jabatan : Staff Gudang
Gaji Pokok        Rp 4.500.000
Tunjangan Makan   Rp   600.000
Total Pendapatan  Rp 5.100.000
Potongan BPJS     Rp   225.000
Total Potongan    Rp   225.000
Gaji Diterima     Rp 4.875.000
JSON:
{"nama_perusahaan": "PT SUMBER REJEKI MAKMUR", "periode": "2025-08", "nama_karyawan": "Andi Saputra", "jabatan": "Staff Gudang", "gaji_pokok": 4500000, "tunjangan_makan": 600000, "tunjangan_transport": null, "total_pendapatan": 5100000, "total_potongan": 225000, "gaji_bersih": 4875000}

OCR TEXT:
SLIP GAJI KARYAWAN
CV MITRA ABADI SENTOSA
PERIODE 0KT0BER2024
Nama  Siti Rahayu   NlK  3201234567890002
A) PENGHASILAN            B) PENGURANGAN
Gajl Pokok  3.000.000     PPh 21          50.000
Uang Makan    450.000     BPJS TK        120.000
GAJI BRUTO  3.450.000     Total Potongan  170.000
PENERIMAAN BERSIH (A-B)   Rp  3.280.000
JSON:
{"nama_perusahaan": "CV MITRA ABADI SENTOSA", "periode": "2024-10", "nama_karyawan": "Siti Rahayu", "nomor_induk_karyawan": "3201234567890002", "gaji_pokok": 3000000, "tunjangan_makan": 450000, "tunjangan_jabatan": null, "total_pendapatan": 3450000, "total_potongan": 170000, "gaji_bersih": 3280000}

OCR TEXT:
DAFTAR GAJI PEGAWAI NEGERI SIPIL
PEMERINTAH KABUPATEN SUKAMAJU
Bulan DESEMBER 2024
Nama       : RIHHIDA LATIFAH PUTRI, S.Pd
NIP        : 198504162010012008
Jabatan    : Guru Ahli Muda
Golongan   : III/c
Status     : PNS
Unit Kerja : SDN 2 Sukamaju
Gaji Pokok            3.150.000
Tunj. Istri/Suami       315.000
Tunj. Anak              126.000
Tunj. Beras             289.000
Jumlah Penghasilan    3.880.000
Potongan BPJS            139.000
PPh Pasal 21             40.000
Jumlah Potongan         179.000
Penghasilan Bersih    3.701.000
JSON:
{"nama_perusahaan": "PEMERINTAH KABUPATEN SUKAMAJU", "periode": "2024-12", "nama_karyawan": "RIHHIDA LATIFAH PUTRI, S.Pd", "nomor_induk_karyawan": "198504162010012008", "jabatan": "Guru Ahli Muda", "divisi": "SDN 2 Sukamaju", "status_pegawai": "PNS", "gaji_pokok": 3150000, "tunjangan_lain": null, "total_pendapatan": 3880000, "total_potongan": 179000, "gaji_bersih": 3701000}

OCR TEXT:
....GAJI KARYAWAN
UD TANI MAKMUR
Periode Juni 2025
Nama Karyawan  : Bambang S
Jabatan        : Sopir
Gaji Pokok            2.800.000
Tunjangan Transport     400.000
[bagian potongan tidak terbaca]
Take Home Pay         3.100.000
JSON:
{"nama_perusahaan": "UD TANI MAKMUR", "periode": "2025-06", "nama_karyawan": "Bambang S", "jabatan": "Sopir", "gaji_pokok": 2800000, "tunjangan_transport": 400000, "tunjangan_makan": null, "total_pendapatan": null, "total_potongan": null, "gaji_bersih": 3100000}

OCR TEXT:
SLIP GAJI KARYAWAN
PT AAE OUTDOOR INDONESIA
PERIODE JUNI 2025
Nama : Putri Indah Sari
NIK : AAE803801
Jabatan : Operator Sewing
Status : Kontrak
A. PENDAPATAN
1. Gaji Pokok         Rp. 2.239.802
2. Tunjangan Jabatan  Rp. 0
3. Tunjangan Makan    Rp. 250.000
4. Lembur             Rp. 486.161
5. THR                Rp. 1.000.000
GAJI BRUTO            Rp. 3.975.963
B. POTONGAN
1. BPJS               Rp. 89.592
2. PPh 21             Rp. 22.398
Total Pengurangan     Rp. 111.990
GAJI NETTO            Rp. 3.863.973
JSON:
{"nama_perusahaan": "PT AAE OUTDOOR INDONESIA", "periode": "2025-06", "nama_karyawan": "Putri Indah Sari", "nomor_induk_karyawan": "AAE803801", "jabatan": "Operator Sewing", "status_pegawai": "Kontrak", "gaji_pokok": 2239802, "tunjangan_jabatan": 0, "tunjangan_makan": 250000, "lembur": 486161, "thr": 1000000, "bonus": null, "total_pendapatan": 3975963, "total_potongan": 111990, "gaji_bersih": 3863973}

OCR TEXT:
PT CAHAYA BAHARI SENTOSA  PAYROLL SLIP - AGUSTUS 2024
ID / Name  4172 / Rizal Hermawan  PTKP  TK/0
Position  : Senior Technician  Kehadiran  22  Shift
Org. Unit  :MAINTENANCE WORKSHOP  Tax ID  :09.254.771.3-402.000
1 Basic Salary  6.200.000  1 BPJS TK  124.000
2 Shift Allw  850.000  2 PPh-21  173.500
3 Upah Persenan  1.250.000  Total Deduction  297.500
4 Jasa Penunjang  400.000
Total Income  8.700.000  Nett Pay  8.402.500
JSON:
{"nama_perusahaan": "PT CAHAYA BAHARI SENTOSA", "periode": "2024-08", "nama_karyawan": "Rizal Hermawan", "nomor_induk_karyawan": "4172", "jabatan": "Senior Technician", "divisi": "MAINTENANCE WORKSHOP", "gaji_pokok": 6200000, "tunjangan_lain": 1250000, "total_pendapatan": 8700000, "total_potongan": 297500, "gaji_bersih": 8402500}

("tunjangan_lain" is 1250000 — the "Upah Persenan" row exactly as printed. It is NOT 850000 + 1250000 + 400000: never add the allowance rows together. The ID "4172" came out of the shared "ID / Name" cell, and every value stopped at its column gap.)

OCR TEXT:
SLIP GAJI
CV BINTANG TIMUR
Nama  : Tomi Sepriadi  Periode : Juli
No. Induk Karyawan  : 07.21.03.19
Bagian  : Produksi
Tanggal cetak  : 05 Agustus 2025
Gaji pokok = Rp5.500.000,-
Total Yang Diterima  Rp.  5.500.000,-
JSON:
{"nama_perusahaan": "CV BINTANG TIMUR", "periode": "2025-07", "nama_karyawan": "Tomi Sepriadi", "nomor_induk_karyawan": "07.21.03.19", "divisi": "Produksi", "gaji_pokok": 5500000, "tunjangan_lain": null, "total_pendapatan": 5500000, "total_potongan": null, "gaji_bersih": 5500000}

(The period line says only "Juli". The year is not on that line, but 2025 is printed elsewhere on the page, so the period is 2025-07 — never answer "????-07". One amount is printed, so it is the basic salary, the total income and the take-home pay at once.)

Now the real page.

OCR TEXT:
{text}
JSON:
