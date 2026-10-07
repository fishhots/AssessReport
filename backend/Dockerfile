FROM maven:3.9-eclipse-temurin-17 AS build

WORKDIR /build
COPY pom.xml ./
COPY settings.xml ./
RUN mvn -B -ntp -s settings.xml dependency:go-offline

COPY src ./src
RUN mvn -B -ntp -s settings.xml -DskipTests package

FROM eclipse-temurin:17-jre-jammy

RUN sed -i 's|archive\.ubuntu\.com|mirrors\.aliyun\.com|g; s|security\.ubuntu\.com|mirrors\.aliyun\.com|g' /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

WORKDIR /app
RUN mkdir -p /app/data && chown -R app:app /app
COPY --from=build --chown=app:app /build/target/*.jar /app/app.jar

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
  CMD curl --fail --silent http://127.0.0.1:8080/api/health >/dev/null || exit 1

ENTRYPOINT ["java", "-jar", "/app/app.jar"]

