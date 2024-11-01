import streamlit as st
from typing import Optional, Dict, Any, List
from shapely.geometry import Point, mapping, shape
import os
import requests
import json
import folium
from streamlit_folium import st_folium

# Constants
BASE_URL: str = "https://gis.unhcr.org/arcgis/rest/services/core_v2/"
COMMON_PARAMS: Dict[str, str] = {'f': 'geojson'}
EXPORT_FOLDER: str = "data"
session: requests.Session = requests.Session()

# Function Definitions
def setup_folder(folder: str) -> None:
    if not os.path.exists(folder):
        os.makedirs(folder)

# Setup folder
setup_folder(EXPORT_FOLDER)

def list_countries() -> List[str]:
    params: Dict[str, str] = {**COMMON_PARAMS, 'where': "1=1", 'outFields': '*', 'returnGeometry': 'false'}
    try:
        response = session.get(BASE_URL + "wrl_prp_a_unhcr/FeatureServer/0/query", params=params)
        response.raise_for_status()
        data: Dict[str, Any] = response.json()
        site_codes: List[str] = [item["properties"]["site_code"][:3] for item in data.get("features", [])]
        country_codes: List[str] = sorted(list(set(site_codes)))
        return country_codes
    except requests.RequestException as e:
        st.error(f"Failed to fetch data: {e}")
        return []
    

def extract_site_codes(country_data: Dict[str, Any]) -> List[str]:
    """
    Extract site codes from country data.

    :param country_data: GeoJSON-like structure containing country features.
    :return: A list of site codes.
    """
    results: List[str] = [item["properties"]["site_code"] for item in country_data.get("features", [])]
    return results

## QUERY POINTS

def query_points(country_code: str, site_codes: List[str]) -> Dict[str, Any]:
    """
    Query points data for a given country code, excluding specific site codes.

    :param country_code: The ISO3 country code to filter by.
    :param site_codes: List of site codes to exclude from the query.
    :return: GeoJSON-like data containing point features.
    """
    site_codes_quoted: List[str] = [f"'{code}'" for code in site_codes]
    where_clause: str = f"iso3='{country_code}' AND pcode NOT IN ({','.join(site_codes_quoted)})"
    url: str = f"{BASE_URL}wrl_prp_p_unhcr_PoC/FeatureServer/0/query"
    params: Dict[str, str] = {
        'where': where_clause,
        'outFields': 'pcode,gis_name',
        'f': 'geojson',
        'returnGeometry': 'true'
    }
    try:
        response = session.get(url, params=params)
        response.raise_for_status()
        data: Dict[str, Any] = response.json()
        # add metadata indicating it is a point
        data['feature_type'] = 'Point'
        return data
    except requests.RequestException as e:
        print(f"Failed to fetch data: {e}")
        return {}

### GET OFFICIAL POLYGONS
def query_polygons(country_code: str,buffer_size_poly: float) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        'where': f"site_code LIKE '{country_code}%'",
        'outFields': 'site_code, name',
        'f': 'geojson',
        'returnGeometry': 'true',
        'geometryType': 'esriGeometryPolygon',
        'outSR': 4326,
    }
    try:
        response = session.get(BASE_URL + "wrl_prp_a_unhcr/FeatureServer/0/query", params=params)
        response.raise_for_status()
        data: Dict[str, Any] = response.json()
        # add metadata indicating it is a polygon
        data['feature_type'] = 'Polygon'
        # Add buffer to each geometry
        for feature in data.get('features', []):
            geometry = shape(feature['geometry'])
            buffered_geometry = geometry.buffer(buffer_size_poly)
            feature['geometry'] = mapping(buffered_geometry)

        return data
    except requests.RequestException as e:
        st.error(f"Failed to fetch data: {e}")
        return {}



def gen_polygons(data: Dict[str, Any], buffer_size: float = 0.01) -> Dict[str, Any]:
    """
    Generates buffered polygons for each feature in the geojson-like data.

    :param data: The input data containing features with geometry of type 'Point'.
    :param buffer_size: The buffer size (in coordinate units) to be used for generating polygons.
    :return: A GeoJSON-like structure with polygons around each point.
    """
    features: List[Dict[str, Any]] = data.get('features', [])
    buffered_features: List[Dict[str, Any]] = []

    for feature in features:
        point_coords: List[float] = feature['geometry']['coordinates']
        point = Point(point_coords)

        # Create a buffer around the point (optionally change to use projected buffer if needed)
        buffer = point.buffer(buffer_size)

        buffered_feature: Dict[str, Any] = {
            'type': 'Feature',
            'geometry': mapping(buffer),  # Use mapping to convert Shapely geometry to GeoJSON format
            'properties': feature['properties']
        }
        buffered_features.append(buffered_feature)

    buffered_geojson: Dict[str, Any] = {
        'type': 'FeatureCollection',
        'features': buffered_features
    }

    assert len(buffered_geojson['features']) == len(features)
    return buffered_geojson

def process_country(country_code: str, buffer_size_points: float, buffer_size_poly: float) -> Optional[Dict[str, Any]]:
    """
    Process the country data by generating polygons and merging them with existing polygons.

    :param country_code: The ISO3 code of the country to process.
    :param buffer_size_points: The size of the buffer to generate polygons from points.
    :return: A combined GeoJSON structure of polygons and generated polygons.
    """
    # Get polygons
    official_polygons: Dict[str, Any] = query_polygons(country_code,buffer_size_poly)
    if not official_polygons:
        print("No data found for the country")
        return None
    else:
        print(f"Successfully fetched {len(official_polygons['features'])} official polygons")
    
    # Add feature type to official polygon features
    for feature in official_polygons['features']:
        feature['properties']['feature_type'] = 'Polygon'
    
    # Get points
    site_codes: List[str] = extract_site_codes(official_polygons)
    points_data: Optional[Dict[str, Any]] = query_points(country_code, site_codes)
    if not points_data or not points_data.get("features"):
        print("No points data found")
        return official_polygons
    else:
        print(f"Successfully fetched {len(points_data['features'])} points")
    
    # Generate points from polygons
    generated_polygons: Dict[str, Any] = gen_polygons(points_data, buffer_size_points)
    
    # Add feature type to generated point polygons
    for feature in generated_polygons.get("features", []):
        feature['properties']['feature_type'] = 'Point'
    
    # Merge polygons
    country_polygons: List[Dict[str, Any]] = official_polygons["features"]
    
    # Add generated polygons if they exist
    if generated_polygons.get("features"):
        country_polygons.extend(generated_polygons["features"])
    
    country_data: Dict[str, Any] = {
        "type": "FeatureCollection",
        "features": country_polygons
    }
   
    return country_data


# Streamlit App with Tabbed Interface
def main():
    st.set_page_config(page_title="UNHCR Geodata Extractor", layout="wide")
    
    # Get current tab from session state or initialize it
    if "active_tab" not in st.session_state:
        st.session_state.active_tab = 0
    
    # Handle query parameters to switch tabs
    query_params = st.experimental_get_query_params()
    if "tab" in query_params:
        try:
            st.session_state.active_tab = int(query_params["tab"][0])
        except ValueError:
            st.session_state.active_tab = 0


    # Create tabs
    tab1, tab2, tab3 = st.tabs([
        "1. Select a country and buffer settings", 
        "2. Review", 
        "3. Export Data"
    ])
    
    with tab1:
        st.header("Select Country")
        # Country Selection
        country_list: List[str] = list_countries()
        country_code = st.selectbox("Choose a country:", country_list, key="country_select")
        
        if country_code:
            # st.success(f"Selected Country: {country_code}")
            st.session_state['selected_country'] = country_code
        else:
            st.warning("Please select a country.")

        st.header("Configure Buffer Settings")
        
        # Ensure a country is selected first
        if 'selected_country' not in st.session_state:
            st.warning("Please select a country in the first tab before configuring buffers.")
        else:
            # Buffer Size Configuration
            buffer_size_points = st.slider(
                "Buffer Size for Points", 
                min_value=0.001, 
                max_value=0.1, 
                value=0.01, 
                step=0.001,
                help="Determines the size of the buffer around point features"
            )
            
            buffer_size_poly = st.slider(
                "Buffer Size for Polygons", 
                min_value=0.001, 
                max_value=0.1, 
                value=0.0, 
                step=0.001,
                help="Determines the size of the buffer around polygon features"
            )
            
            if st.button("Process Geodata", key="process_data"):
                with st.spinner("Processing geodata..."):
                    country_data = process_country(
                        st.session_state['selected_country'], 
                        buffer_size_points, 
                        buffer_size_poly
                    )
                    
                    if country_data:
                        st.session_state['country_data'] = country_data
                        st.success("Data processed successfully!")
                        st.experimental_set_query_params(tab=3)
                    else:
                        st.error("Failed to process country data.")

            
    with tab2:
        st.header("Visualize Geodata")
        
        # Ensure data has been processed
        if 'country_data' not in st.session_state:
            st.warning("Please process data in the previous tab.")
        else:
            features = st.session_state['country_data']['features']
            
            # Feature selection and map display
            all_feature_labels = [
                f"{feature['properties'].get('name', 'N/A')} ({feature['properties'].get('feature_type', 'N/A')})" 
                for feature in features
            ]
            
            col1, col2 = st.columns([1, 2])
            
            with col1:
                selected_label = st.selectbox(
                    "Select a feature to view:", 
                    all_feature_labels
                )
            
            # Map rendering logic remains the same as in previous script
            selected_feature = next(
                (feature for feature, label in zip(features, all_feature_labels) if label == selected_label), 
                None
            )
            
            if selected_feature is not None:
                with col2:
                    selected_feature_geometry = selected_feature['geometry']

                    # Get coordinates for map centering
                    if selected_feature_geometry['type'] == 'Polygon':
                        coordinates = selected_feature_geometry['coordinates'][0][0]
                    elif selected_feature_geometry['type'] == 'MultiPolygon':
                        coordinates = selected_feature_geometry['coordinates'][0][0][0]
                    else:
                        coordinates = selected_feature_geometry['coordinates']

                    # Create and display map
                    m = folium.Map(location=[coordinates[1], coordinates[0]], zoom_start=12)
                    folium.TileLayer(
                        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                        attr='ArcGIS World Imagery'
                    ).add_to(m)

                    # Add features to map
                    for feature in features:
                        if feature['geometry']['type'] in ['Polygon', 'MultiPolygon']:
                            style = (
                                {'color': 'red', 'weight': 3} 
                                if feature == selected_feature 
                                else {'color': 'blue', 'weight': 2}
                            )
                            folium.GeoJson(
                                feature,
                                style_function=lambda x, style=style: style
                            ).add_to(m)
                    
                    st_folium(m, width=700, height=500)
    
    with tab3:
        st.header("Export Geodata")
        
        # Ensure data has been processed
        if 'country_data' not in st.session_state:
            st.warning("Please process data in the previous tabs.")
        else:
            features = st.session_state['country_data']['features']
            
            # Fix renaming
            for feature in features:
                if 'pcode' in feature['properties']:
                    feature['properties']['site_code'] = feature['properties'].pop('pcode')
                if 'gis_name' in feature['properties']:
                    feature['properties']['name'] = feature['properties'].pop('gis_name')
            
            all_feature_labels = [
                f"{feature['properties'].get('name', 'N/A')} ({feature['properties'].get('feature_type', 'N/A')})" 
                for feature in features
            ]
            
            st.write("### Select Features to Export")
            
            # Select features to export
            selected_features_to_export = st.multiselect(
                "Select features to export:",
                options=all_feature_labels,
                default=all_feature_labels if st.checkbox("Select All Features") else []
            )
            
            # Download selected features
            if st.button("Generate GeoJSON file"):
                if not selected_features_to_export:
                    st.error("No feature selected. Please select at least one feature.")
                else:
                    # Create export folder if it doesn't exist
                    if not os.path.exists(EXPORT_FOLDER):
                        os.makedirs(EXPORT_FOLDER)
                    
                    filtered_output_file = f"{EXPORT_FOLDER}/{st.session_state['selected_country']}_filtered_polygons.geojson"
                    
                    filtered_features = [
                        feature for feature, label in zip(features, all_feature_labels)
                        if label in selected_features_to_export
                    ]

                    filtered_data = {
                        "type": "FeatureCollection",
                        "features": filtered_features
                    }

                    with open(filtered_output_file, 'w') as f:
                        json.dump(filtered_data, f, indent=4)

                    # Directly download the file
                    with open(filtered_output_file, 'r') as f:
                        st.download_button(
                            label="Download Filtered GeoJSON",
                            data=f,
                            file_name=f"{st.session_state['selected_country']}_filtered_polygons.geojson",
                            mime="application/geo+json"
                        )
                    
                    st.success(f"Exported {len(filtered_features)} features to {filtered_output_file}")

# Run the Streamlit app
if __name__ == "__main__":
    main()
