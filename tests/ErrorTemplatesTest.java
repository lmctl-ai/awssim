package ai.lmctl.awssim;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.StringReader;
import javax.xml.parsers.DocumentBuilderFactory;
import org.xml.sax.InputSource;

public class ErrorTemplatesTest {
    public static void main(String[] args) throws Exception {
        String value = "quotes \" ' & < > 雪 \\ newline\n%s ${message}";
        var json = new ObjectMapper().readTree(ErrorTemplates.render(false, value, value));
        if (!value.equals(json.get("__type").asText()) || !value.equals(json.get("message").asText()))
            throw new AssertionError("JSON round trip");
        var doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(
                new InputSource(new StringReader(ErrorTemplates.render(true, value, value))));
        if (!value.equals(doc.getElementsByTagName("Code").item(0).getTextContent())
                || !value.equals(doc.getElementsByTagName("Message").item(0).getTextContent()))
            throw new AssertionError("XML round trip");
        System.out.println("JSON/XML error templates: special characters round-trip without rescanning");
    }
}
