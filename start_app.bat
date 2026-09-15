@echo off
title TestDataPlotter
echo Starting Test Data Plotter...
echo.
echo The app will open in your browser at http://localhost:5002
echo Close this window to stop the server.
echo.
cd /d "%~dp0"
streamlit run test_data_plotter.py --server.headless=true
pause