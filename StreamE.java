import java.util.*;
class StreamEx {
    public static int prime (int x) {
        if (x < 2) return 0;
        for (int i = 2; i <= Math.sqrt(x); i++) {
            if (x % i == 0) return 0;
        }
        return 1;
    }

    public static void main (String [] args) {
        Scanner sc = new Scanner(System.in);
        System.out.println("Enter number of elements: ");
        int n = sc.nextInt();
        List<Integer> list = new ArrayList<>();
        System.out.println("Enter " + n + " elements: ");
        for (int i = 0; i < n; i++) {
            list.add(sc.nextInt());
        }
        int sum = list.stream()
                        .mapToInt(Integer::intValue)
                        .sum();
        
        double average = list.stream()
                             .mapToInt(Integer::intValue)
                             .average()
                             .orElse(0.0);

        int max = list.stream()
                      .mapToInt(Integer::intValue)
                      .max()
                      .orElse(Integer.MIN_VALUE);
 
        long even_cnt = list.stream()
                             .filter(x -> x % 2 == 0)
                             .count();
                        
        System.out.println("Prime numbers: ");
        list.stream()
            .filter(x -> prime(x) == 1)
            .forEach(x -> System.out.print(x + " "));

        System.out.println("\n 3 digit numbers: ");
        list.stream()
            .filter(x -> x >= 100 && x <= 999)
            .forEach(x -> System.out.print(x + " "));
        
        List<String> words = Arrays.asList("sujana", "suhas", "suman", "sumith", "lanka", "sumukhi", "kalyani", "kaushik");
        System.out.println("\nNames starting with 'su': ");
        words.stream()
             .filter(x -> x.startsWith("su"))
             .forEach(x -> System.out.println(x));

        System.out.println("\nNumbers: " + list);
        System.out.println("Sum: " + sum);
        System.out.printf("Average: %.2f\n", average);
        System.out.println("Max: " + max);
        System.out.println("Even Count: " + even_cnt);
    }
}