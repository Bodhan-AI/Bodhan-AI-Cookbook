#!/bin/bash
# Convenient launcher for Bodhan Reader Streamlit app
# Uses the multimedia conda environment

CONDA_ENV="multimedia"

if command -v conda &> /dev/null; then
    echo "Starting Bodhan Reader in conda environment '$CONDA_ENV'..."
    conda run -n $CONDA_ENV streamlit run streamlit_app.py
elif [ -f "/opt/anaconda3/envs/$CONDA_ENV/bin/streamlit" ]; then
    echo "Starting Bodhan Reader using /opt/anaconda3/envs/$CONDA_ENV/bin/streamlit..."
    /opt/anaconda3/envs/$CONDA_ENV/bin/streamlit run streamlit_app.py
else
    echo "Starting with system streamlit..."
    streamlit run streamlit_app.py
fi

