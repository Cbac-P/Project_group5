@echo off
setlocal
chcp 65001 >nul
title XemAnime - Crawler ca nhan
pushd "%~dp0"
if errorlevel 1 exit /b 1
set "crawler_python=.venv\Scripts\python.exe"
if exist "%crawler_python%" goto dependencies
echo Dang tao moi truong Python...
where py >nul 2>&1
if errorlevel 1 goto use_python
py -3 -m venv .venv
if errorlevel 1 goto setup_error
goto dependencies
:use_python
python -m venv .venv
if errorlevel 1 goto setup_error
:dependencies
"%crawler_python%" -c "import sys;sys.exit(sys.version_info < (3,10))" >nul 2>&1
if errorlevel 1 goto setup_error
"%crawler_python%" -c "import requests,bs4" >nul 2>&1
if not errorlevel 1 goto menu
"%crawler_python%" -m pip install -r requirements.txt
if errorlevel 1 goto setup_error
:menu
echo.
echo ===== XEMANIME CRAWLER - DATASET 9 TRUONG =====
echo 1. Crawl / tiep tuc cau hinh da luu - moi 100 trang
echo 2. Mo rong ngan sach toi it nhat 300 trang
echo 3. Xem thong ke tu SQLite
echo 4. Xuat lai JSONL va CSV
echo 5. Kiem tra DB, BFS, robots va dataset
echo 6. Mo thu muc data
echo 7. Dong goi dataset cua minh de nop GitHub
echo 8. Chay kiem thu offline
echo 9. Mo README
echo M. Xem va kiem tra du lieu mau di kem ZIP
echo 0. Thoat
echo Gioi han trang la tong tich luy, khong phai so trang them moi.
choice /c 123456789M0 /n /m "Chon: "
if errorlevel 11 goto finish
if errorlevel 10 goto sample
if errorlevel 9 goto readme
if errorlevel 8 goto tests
if errorlevel 7 goto pack
if errorlevel 6 goto folder
if errorlevel 5 goto verify
if errorlevel 4 goto export
if errorlevel 3 goto report
if errorlevel 2 goto expanded
if errorlevel 1 goto pilot
:pilot
"%crawler_python%" -X utf8 main.py crawl
goto result
:expanded
"%crawler_python%" -X utf8 main.py crawl --profile expanded
goto result
:report
"%crawler_python%" -X utf8 main.py report
goto result
:sample
"%crawler_python%" -X utf8 main.py report --db sample_data\crawler.db --output sample_data
if errorlevel 1 goto result
"%crawler_python%" -X utf8 main.py verify --db sample_data\crawler.db --output sample_data
goto result
:export
"%crawler_python%" -X utf8 main.py export
goto result
:verify
"%crawler_python%" -X utf8 main.py verify
goto result
:tests
"%crawler_python%" -X utf8 -m unittest discover -s tests -v
goto result
:pack
echo Dong goi dataset cua Nguyen Viet Phuong - MSSV CE190248.
"%crawler_python%" -X utf8 main.py pack
goto result
:folder
if not exist data mkdir data
start "" "%CD%\data"
goto menu
:readme
start "" "%CD%\README.md"
goto menu
:result
if errorlevel 1 (
    echo Chua thanh cong. Xem loi phia tren.
) else (
    echo Hoan tat.
)
goto menu
:setup_error
echo Can Python 3.10 tro len; mang chi can khi cai thu vien / crawl.
pause
popd
endlocal
exit /b 1
:finish
popd
endlocal
exit /b 0
