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

#####################################
# Streamlit App
st.title("UNHCR Geodata Extractor")

country_list: List[str] = list_countries()
country_code = st.sidebar.selectbox("Select a country:", country_list)

buffer_size_points = st.sidebar.slider("Select buffer size for points", min_value=0.001, max_value=0.1, value=0.01, step=0.001)

buffer_size_poly = st.sidebar.slider("Select buffer size for polygons", min_value=0.001, max_value=0.1, value=0.0, step=0.001)

if st.sidebar.button("Process Country"):
    if country_code:
        st.write(f"Processing country: {country_code} with buffer size for points: {buffer_size_points}")
        country_data = process_country(country_code, buffer_size_points,buffer_size_poly)
        if country_data:
            st.session_state['country_data'] = country_data
    else:
        st.warning("Please select a country")

# Display Features and Export Option
if 'country_data' in st.session_state:
    features = st.session_state['country_data']['features']
    
    # Display all features as a single list if filtering is not applicable
    all_feature_labels = [f"{feature['properties'].get('name', 'N/A')} ({feature['properties'].get('feature_type', 'N/A')})" for feature in features]
    
    # Feature selection for viewing details
    selected_label = st.selectbox("Select a feature to view details:", all_feature_labels)
    
    # Check if a valid selection was made
    selected_feature = next((feature for feature, label in zip(features, all_feature_labels) if label == selected_label), None)
    
    # Only proceed if a feature was found
    if selected_feature is not None:
        # Extract geometry from selected feature
        selected_feature_geometry = selected_feature['geometry']

        # Get coordinates for map centering based on geometry type
        if selected_feature_geometry['type'] == 'Polygon':
            coordinates = selected_feature_geometry['coordinates'][0][0]
        elif selected_feature_geometry['type'] == 'MultiPolygon':
            coordinates = selected_feature_geometry['coordinates'][0][0][0]
        else:
            coordinates = selected_feature_geometry['coordinates']  # For Point geometries

        # Create and display map
        m = folium.Map(location=[coordinates[1], coordinates[0]], zoom_start=12, width='100%', height='700')
        folium.TileLayer(
            tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            attr='ArcGIS World Imagery'
        ).add_to(m)

        # Add all features to the map
        for feature in features:
            if feature['geometry']['type'] in ['Polygon', 'MultiPolygon']:
                # Highlight selected feature
                style = {'color': 'red', 'weight': 3} if feature == selected_feature else {'color': 'blue', 'weight': 2}
                folium.GeoJson(
                    feature,
                    style_function=lambda x, style=style: style
                ).add_to(m)
        
        st_folium(m, width=900, height=600)
    
    else:
        st.warning("Please select a valid feature to view details.")

    # Fix renaming
    for feature in features:
        if 'pcode' in feature['properties']:
            feature['properties']['site_code'] = feature['properties'].pop('pcode')
        if 'gis_name' in feature['properties']:
            feature['properties']['name'] = feature['properties'].pop('gis_name')

    # Select features to export
    st.write("### Select Features to Export")

    # Selection for export
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
            filtered_output_file = f"{EXPORT_FOLDER}/{country_code}_filtered_polygons.geojson"
            filtered_features = [
                feature for feature, label in zip(features, all_feature_labels)
                if label in selected_features_to_export
            ]

            filtered_data = {
                "type": "FeatureCollection",
                "features": filtered_features
            }

            # display message
            st.success(f"Exported {len(filtered_features)} features to {filtered_output_file}")

            with open(filtered_output_file, 'w') as f:
                json.dump(filtered_data, f, indent=4)

            # Directly download the file
            with open(filtered_output_file, 'r') as f:
                st.download_button(
                    label="Download Filtered GeoJSON",
                    data=f,
                    file_name=f"{country_code}_filtered_polygons.geojson",
                    mime="application/geo+json"
                )
