#include <Arduino.h>
#include <WiFi.h>
#include <sMQTTBroker.h>
#include <ArduinoJson.h> 
#include <WiFiManager.h> //

// --- LilyGo EPD47 Includes ---
#include "epd_driver.h"
#include "firasans.h" 

const char* MQTT_CLIENT_USER = "User_Name";
const char* MQTT_CLIENT_PASSWORD = "You_Password";

// --- Hardware & Pin Configuration ---
#define BUTTON_1_PIN 39     // Info / Context Menu
#define BUTTON_2_PIN 34     // Clear / Back 
#define BUTTON_3_PIN 35     // WiFi Portal Setup

// --- State Management ---
bool showingCredentials = false;
unsigned long credentialTimer = 0; 
bool showingMessage = false;
bool inSetupMode = false;    

// Decoupled Network State from Screen State
bool isNetworkAP = false;    
bool showingAPInfo = false;  

// --- E-Paper Globals ---
uint8_t *framebuffer; uint8_t *credBuffer; uint8_t *msgBuffer;   
int cursor_y = 120; const int MAX_Y = 500; 
String currentIP = "0.0.0.0";

// --- HELPER: Word-Wraps long text ---
void drawWrappedText(String text, int startX, int &currentY, uint8_t *buffer, int maxCharsPerLine = 45) {
    text.replace("\n", " "); text.replace("\r", "");
    String currentLine = ""; int lastSpaceIdx = 0; int spaceIdx = 0;
    while ((spaceIdx = text.indexOf(' ', lastSpaceIdx)) != -1) {
        String word = text.substring(lastSpaceIdx, spaceIdx);
        if (currentLine.length() + word.length() + 1 > maxCharsPerLine) {
            int tempX = startX; writeln((GFXfont *)&FiraSans, (char *)currentLine.c_str(), &tempX, &currentY, buffer);
            currentY += 45; currentLine = word;
        } else { if (currentLine.length() > 0) currentLine += " "; currentLine += word; }
        lastSpaceIdx = spaceIdx + 1;
    }
    String lastWord = text.substring(lastSpaceIdx);
    if (currentLine.length() + lastWord.length() + 1 > maxCharsPerLine) {
        int tempX = startX; writeln((GFXfont *)&FiraSans, (char *)currentLine.c_str(), &tempX, &currentY, buffer);
        currentY += 45; currentLine = lastWord;
    } else { if (currentLine.length() > 0) currentLine += " "; currentLine += lastWord; }
    if (currentLine.length() > 0) { int tempX = startX; writeln((GFXfont *)&FiraSans, (char *)currentLine.c_str(), &tempX, &currentY, buffer); }
}

void drawHeader() {
    int h_x = 20; int h_y = 40;
    String line1 = "IP: " + currentIP + "   |   Port: 1883";
    writeln((GFXfont *)&FiraSans, (char *)line1.c_str(), &h_x, &h_y, framebuffer);
    h_x = 20; h_y += 30;
    writeln((GFXfont *)&FiraSans, "------------------------------------------------------------------", &h_x, &h_y, framebuffer);
}

void configModeCallback (WiFiManager *myWiFiManager) {
    inSetupMode = true; 
    memset(msgBuffer, 255, EPD_WIDTH * EPD_HEIGHT / 2); 
    int m_x = 50; int m_y = 150;
    writeln((GFXfont *)&FiraSans, "--- WIFI SETUP ACTIVE ---", &m_x, &m_y, msgBuffer);
    m_x = 50; m_y += 60;
    String info = "Connect WiFi: " + myWiFiManager->getConfigPortalSSID();
    writeln((GFXfont *)&FiraSans, (char *)info.c_str(), &m_x, &m_y, msgBuffer);
    m_x = 50; m_y += 60;
    writeln((GFXfont *)&FiraSans, "Then go to: 192.168.4.1", &m_x, &m_y, msgBuffer);
    m_x = 50; m_y += 80;
    writeln((GFXfont *)&FiraSans, "(Will auto-close in 60s)", &m_x, &m_y, msgBuffer);
    epd_poweron(); epd_clear(); epd_draw_grayscale_image(epd_full_screen(), msgBuffer); epd_poweroff();
}

void setupCredentialBuffer() {
    memset(credBuffer, 255, EPD_WIDTH * EPD_HEIGHT / 2);
    int c_x = 300; int c_y = 200;
    writeln((GFXfont *)&FiraSans, "--- MQTT Credentials ---", &c_x, &c_y, credBuffer);
    c_x = 350; c_y += 60;
    String userStr = "User: " + String(MQTT_CLIENT_USER);
    writeln((GFXfont *)&FiraSans, (char *)userStr.c_str(), &c_x, &c_y, credBuffer);
    c_x = 350; c_y += 60;
    String passStr = "Pass: " + String(MQTT_CLIENT_PASSWORD);
    writeln((GFXfont *)&FiraSans, (char *)passStr.c_str(), &c_x, &c_y, credBuffer);
}

void drawAPInfoScreen() {
    memset(msgBuffer, 255, EPD_WIDTH * EPD_HEIGHT / 2); 
    int m_x = 50; int m_y = 150;
    writeln((GFXfont *)&FiraSans, "--- FIELD AP ACTIVE ---", &m_x, &m_y, msgBuffer);
    
    m_x = 50; m_y += 60; String info = "Connect WiFi: T5_Field_Broker";
    writeln((GFXfont *)&FiraSans, (char *)info.c_str(), &m_x, &m_y, msgBuffer);
    
    // FIX: Added the WiFi Password to the display so you don't forget it
    m_x = 50; m_y += 60; String wifiPassStr = "WiFi Pass: " + String(MQTT_CLIENT_PASSWORD);
    writeln((GFXfont *)&FiraSans, (char *)wifiPassStr.c_str(), &m_x, &m_y, msgBuffer);
    
    m_x = 50; m_y += 60; String ipInfo = "Broker IP: " + currentIP;
    writeln((GFXfont *)&FiraSans, (char *)ipInfo.c_str(), &m_x, &m_y, msgBuffer);
    
    // Extra Credentials for setting up new nodes
    m_x = 50; m_y += 60; String userStr = "MQTT User: " + String(MQTT_CLIENT_USER);
    writeln((GFXfont *)&FiraSans, (char *)userStr.c_str(), &m_x, &m_y, msgBuffer);
    m_x = 50; m_y += 60; String passStr = "MQTT Pass: " + String(MQTT_CLIENT_PASSWORD);
    writeln((GFXfont *)&FiraSans, (char *)passStr.c_str(), &m_x, &m_y, msgBuffer);
    
    epd_poweron(); epd_clear(); epd_draw_grayscale_image(epd_full_screen(), msgBuffer); epd_poweroff();
}

void printMessage(String text) {
    bool needsClear = false;
    if (cursor_y > MAX_Y) { 
        needsClear = true; memset(framebuffer, 255, EPD_WIDTH * EPD_HEIGHT / 2); 
        drawHeader(); cursor_y = 120; 
    }
    int cursor_x = 20; writeln((GFXfont *)&FiraSans, (char *)text.c_str(), &cursor_x, &cursor_y, framebuffer);
    
    if (!showingCredentials && !showingMessage && !showingAPInfo && !inSetupMode) {
        epd_poweron(); if (needsClear) epd_clear();
        epd_draw_grayscale_image(epd_full_screen(), framebuffer); epd_poweroff();
    }
    cursor_y += 40;
}

class MyBroker:public sMQTTBroker {
public:
    bool onEvent(sMQTTEvent *event) override {
        switch(event->Type()) {
            case NewClient_sMQTTEventType: { 
                sMQTTNewClientEvent *e=(sMQTTNewClientEvent*)event;
                printMessage("Node Joined: [" + String(e->Login().c_str()) + "]");
                if ((e->Login() != MQTT_CLIENT_USER) || (e->Password() != MQTT_CLIENT_PASSWORD)) return false;
            } break;

            case Public_sMQTTEventType: { 
                sMQTTPublicClientEvent *e = (sMQTTPublicClientEvent*)event;
                String topic = e->Topic().c_str();
                String payload = e->Payload().c_str();
                
                printMessage("[" + topic + "]"); 

                DynamicJsonDocument doc(2048);
                DeserializationError error = deserializeJson(doc, payload);
                if (!error && doc["payload"].containsKey("text")) {
                    String actualText = doc["payload"]["text"].as<String>();
                    String sender = doc["from"].as<String>(); 
                    memset(msgBuffer, 255, EPD_WIDTH * EPD_HEIGHT / 2); 
                    int m_x = 50; int m_y = 100;
                    writeln((GFXfont *)&FiraSans, "--- NEW MESSAGE ---", &m_x, &m_y, msgBuffer);
                    m_x = 50; m_y += 60;
                    String senderStr = "From Node: " + sender;
                    writeln((GFXfont *)&FiraSans, (char *)senderStr.c_str(), &m_x, &m_y, msgBuffer);
                    m_y += 60; drawWrappedText(actualText, 50, m_y, msgBuffer, 45);

                    showingMessage = true; showingAPInfo = false; showingCredentials = false; inSetupMode = false;
                    epd_poweron(); epd_clear(); epd_draw_grayscale_image(epd_full_screen(), msgBuffer); epd_poweroff();
                }
            } break;
        }
        return true;
    }
};

MyBroker broker;

void setup() {
    Serial.begin(115200);
    pinMode(BUTTON_1_PIN, INPUT); 
    pinMode(BUTTON_2_PIN, INPUT); 
    pinMode(BUTTON_3_PIN, INPUT); 

    epd_init();
    framebuffer = (uint8_t *)heap_caps_malloc(EPD_WIDTH * EPD_HEIGHT / 2, MALLOC_CAP_SPIRAM);
    credBuffer = (uint8_t *)heap_caps_malloc(EPD_WIDTH * EPD_HEIGHT / 2, MALLOC_CAP_SPIRAM);
    msgBuffer = (uint8_t *)heap_caps_malloc(EPD_WIDTH * EPD_HEIGHT / 2, MALLOC_CAP_SPIRAM);
    
    setupCredentialBuffer(); memset(framebuffer, 255, EPD_WIDTH * EPD_HEIGHT / 2); 
    epd_poweron(); epd_clear(); epd_poweroff();

    WiFiManager wm; 
    wm.setAPCallback(configModeCallback);
    wm.setConfigPortalTimeout(30); 

    if(!wm.autoConnect("T5_Broker_Setup")) {
        currentIP = "DISCONNECTED";
        drawHeader();
        printMessage("No WiFi found.");
        printMessage("Use B1+B2 for Field AP.");
    } else {
        currentIP = WiFi.localIP().toString(); 
        drawHeader(); 
        printMessage("System Ready.");
    }
    
    broker.init(1883); 
};

void loop() {
    broker.update();
    
    bool b1 = (digitalRead(BUTTON_1_PIN) == LOW);
    bool b2 = (digitalRead(BUTTON_2_PIN) == LOW);
    bool b3 = (digitalRead(BUTTON_3_PIN) == LOW);

    if (showingCredentials && (millis() - credentialTimer >= 10000)) {
        showingCredentials = false;
        epd_poweron(); epd_clear(); epd_draw_grayscale_image(epd_full_screen(), framebuffer); epd_poweroff();
    }

    // --- LOGIC 1: TWO-BUTTON COMBO (START FIELD AP) ---
    if (b1 && b2 && !isNetworkAP && !inSetupMode) {
        unsigned long comboStart = millis();
        while(digitalRead(BUTTON_1_PIN) == LOW && digitalRead(BUTTON_2_PIN) == LOW) {
            if (millis() - comboStart > 2000) { 
                isNetworkAP = true;
                showingAPInfo = true;
                showingCredentials = false; 
                showingMessage = false;
                
                WiFi.disconnect(true); 
                WiFi.mode(WIFI_AP); 
                delay(100); 
                
                // FIX: Use the MQTT Password as the WiFi AP Password
                WiFi.softAP("T5_Field_Broker", MQTT_CLIENT_PASSWORD);
                currentIP = WiFi.softAPIP().toString();
                
                memset(framebuffer, 255, EPD_WIDTH * EPD_HEIGHT / 2); 
                drawHeader();
                cursor_y = 120; 
                printMessage("Field AP Started."); 
                
                drawAPInfoScreen();
                
                while (digitalRead(BUTTON_1_PIN) == LOW || digitalRead(BUTTON_2_PIN) == LOW) { delay(10); }
                delay(500); 
                break; 
            }
        }
    }
    
    // --- LOGIC 2: INFO BUTTON (CONTEXT AWARE) ---
    else if (b1 && !b2 && !showingCredentials && !showingMessage && !showingAPInfo && !inSetupMode) {
        if (isNetworkAP) {
            showingAPInfo = true;
            drawAPInfoScreen();
        } else {
            showingCredentials = true; credentialTimer = millis(); 
            epd_poweron(); epd_clear(); epd_draw_grayscale_image(epd_full_screen(), credBuffer); epd_poweroff();
        }
        while(digitalRead(BUTTON_1_PIN) == LOW) delay(10);
        delay(200);
    }
    
    // --- LOGIC 3: CLEAR/BACK BUTTON ---
    else if ((b1 || b2) && !(b1 && b2) && (showingMessage || showingAPInfo || showingCredentials)) {
        showingMessage = false; 
        showingAPInfo = false; 
        showingCredentials = false;
        
        epd_poweron(); epd_clear(); epd_draw_grayscale_image(epd_full_screen(), framebuffer); epd_poweroff();
        while(digitalRead(BUTTON_1_PIN) == LOW || digitalRead(BUTTON_2_PIN) == LOW) delay(10);
        delay(200);
    }
    
    // --- LOGIC 4: WIFI SETUP PORTAL ---
    else if (b3 && !inSetupMode && !showingMessage && !showingAPInfo && !isNetworkAP) {
        WiFiManager wm; 
        wm.setAPCallback(configModeCallback);
        wm.setConfigPortalTimeout(60); 
        wm.startConfigPortal("T5_Broker_Setup");
        
        inSetupMode = false;
        epd_poweron(); epd_clear(); epd_draw_grayscale_image(epd_full_screen(), framebuffer); epd_poweroff();
    }

    delay(10); 
}